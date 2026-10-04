/*
 * DigiClinic — local WhatsApp gateway (whatsapp-web.js).
 *
 * Listens on 127.0.0.1 only; every request needs the X-Api-Key header. One WhatsApp session per
 * organization (`/sessions/:id/...`). Events (ready / disconnected / message acks) are POSTed to
 * Django's webhook, signed with HMAC-SHA256 over the raw body.
 *
 *   GET  /sessions/:id           → { state, qr, me }
 *   POST /sessions/:id/start     → begins linking (QR appears in GET)
 *   POST /sessions/:id/logout    → unlinks the phone
 *   POST /sessions/:id/send      → { to, caption, filename?, pdf_base64? } → { id }
 */
"use strict";

// Must run before whatsapp-web.js is loaded (see patch-wwebjs.js).
console.log("wwebjs media fix:", require("./patch-wwebjs")());

const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const express = require("express");
const QRCode = require("qrcode");
const { Client, LocalAuth, MessageMedia } = require("whatsapp-web.js");

// Share settings with Django: read KEY=VALUE lines from the project's .env (env vars win).
try {
  for (const line of fs.readFileSync(path.join(__dirname, "..", ".env"), "utf8").split(/\r?\n/)) {
    const m = /^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/.exec(line);
    if (m && process.env[m[1]] === undefined) process.env[m[1]] = m[2];
  }
} catch (_) {
  /* no .env */
}

const PORT = Number(process.env.WA_GATEWAY_PORT || 3320);
const API_KEY = process.env.WA_GATEWAY_KEY || "";
const WEBHOOK_URL = process.env.WA_WEBHOOK_URL || "http://127.0.0.1:8010/integrations/whatsapp/webhook/";
const WEBHOOK_SECRET = process.env.WA_WEBHOOK_SECRET || API_KEY;
const DATA_DIR =
  process.env.WA_DATA_DIR || path.join(process.env.LOCALAPPDATA || os.homedir(), "digiclinic", "whatsapp");

if (!API_KEY) {
  console.error("WA_GATEWAY_KEY is not set. Refusing to start.");
  process.exit(1);
}

function findChrome() {
  if (process.env.CHROME_PATH && fs.existsSync(process.env.CHROME_PATH)) return process.env.CHROME_PATH;
  // Reuse the Chromium that Playwright installed for PDFs. The headless shell is preferred: on some
  // Windows setups the full chrome.exe can't be spawned from Node (spawn UNKNOWN).
  const base = path.join(process.env.LOCALAPPDATA || "", "ms-playwright");
  try {
    const dirs = fs.readdirSync(base).sort().reverse();
    const candidates = [
      ...dirs.filter((d) => d.startsWith("chromium_headless_shell-")).map((d) =>
        path.join(base, d, "chrome-headless-shell-win64", "chrome-headless-shell.exe")),
      ...dirs.filter((d) => d.startsWith("chromium-")).map((d) => path.join(base, d, "chrome-win64", "chrome.exe")),
    ];
    for (const exe of candidates) if (fs.existsSync(exe)) return exe;
  } catch (_) {
    /* fall through */
  }
  return undefined; // puppeteer's own download, if any
}

const sessions = new Map(); // id -> { client, state, qr, me }

async function notify(payload) {
  const body = JSON.stringify(payload);
  const signature = crypto.createHmac("sha256", WEBHOOK_SECRET).update(body).digest("hex");
  try {
    await fetch(WEBHOOK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Signature": signature },
      body,
    });
  } catch (err) {
    console.warn("webhook failed:", err.message);
  }
}

function getSession(id) {
  if (!sessions.has(id)) sessions.set(id, { client: null, state: "disconnected", qr: null, me: null });
  return sessions.get(id);
}

function start(id) {
  const s = getSession(id);
  if (s.client) return s;
  s.state = "starting";
  const client = new Client({
    authStrategy: new LocalAuth({ clientId: `org-${id}`, dataPath: DATA_DIR }),
    puppeteer: { headless: true, executablePath: findChrome(), args: ["--no-sandbox"] },
  });
  s.client = client;

  client.on("qr", async (qr) => {
    s.state = "qr";
    s.qr = await QRCode.toDataURL(qr, { margin: 1, width: 280 });
  });
  const markReady = () => {
    if (s.state === "ready") return;
    s.state = "ready";
    s.qr = null;
    s.me = client.info && client.info.wid ? client.info.wid.user : s.me;
    notify({ event: "status", session: id, state: "ready", me: s.me });
  };
  client.on("authenticated", () => {
    s.state = "connecting";
    s.qr = null;
    // Newer WhatsApp Web builds sometimes never fire "ready". Watch the real connection state instead.
    const watchdog = setInterval(async () => {
      if (s.client !== client || s.state === "ready" || s.state === "disconnected") return clearInterval(watchdog);
      try {
        if ((await client.getState()) === "CONNECTED") {
          if (!s.me) {
            s.me = await client.pupPage.evaluate(() => {
              const me = window.require("WAWebUserPrefsMeUser").getMaybeMePnUser?.() ||
                window.require("WAWebUserPrefsMeUser").getMaybeMeUser?.();
              return me ? me.user : null;
            }).catch(() => null);
          }
          clearInterval(watchdog);
          markReady();
        }
      } catch (_) {
        /* page still loading */
      }
    }, 5000);
  });
  client.on("ready", markReady);
  client.on("auth_failure", (msg) => {
    s.state = "disconnected";
    notify({ event: "status", session: id, state: "auth_failure", error: String(msg) });
  });
  client.on("disconnected", async (reason) => {
    s.state = "disconnected";
    s.qr = null;
    s.me = null;
    notify({ event: "status", session: id, state: "disconnected", error: String(reason) });
    try {
      await client.destroy();
    } catch (_) {
      /* already gone */
    }
    s.client = null;
  });
  client.on("message_ack", (msg, ack) => {
    // 1 = sent to server, 2 = delivered, 3 = read, 4 = played
    notify({ event: "ack", session: id, id: msg.id._serialized, ack });
  });

  client.initialize().catch((err) => {
    console.error(`session ${id} failed to start:`, err.message);
    s.state = "error";
    s.client = null;
  });
  return s;
}

const app = express();
app.use(express.json({ limit: "25mb" }));
app.use((req, res, next) => {
  const given = Buffer.from(req.get("X-Api-Key") || "");
  const expected = Buffer.from(API_KEY);
  if (given.length !== expected.length || !crypto.timingSafeEqual(given, expected)) {
    return res.status(401).json({ error: "unauthorized" });
  }
  next();
});

app.get("/health", (req, res) => res.json({ ok: true }));

app.get("/sessions/:id", (req, res) => {
  const s = getSession(req.params.id);
  res.json({ state: s.state, qr: s.qr, me: s.me });
});

app.post("/sessions/:id/start", (req, res) => {
  const s = start(req.params.id);
  res.json({ state: s.state });
});

app.post("/sessions/:id/logout", async (req, res) => {
  const s = getSession(req.params.id);
  if (s.client) {
    try {
      await s.client.logout();
    } catch (_) {
      /* ignore */
    }
    try {
      await s.client.destroy();
    } catch (_) {
      /* ignore */
    }
  }
  sessions.delete(req.params.id);
  res.json({ state: "disconnected" });
});

app.post("/sessions/:id/send", async (req, res) => {
  const s = getSession(req.params.id);
  if (!s.client || s.state !== "ready") return res.status(409).json({ error: "not_ready" });
  const { to, caption, filename, pdf_base64: pdf } = req.body || {};
  if (!/^\d{8,15}$/.test(String(to || ""))) return res.status(400).json({ error: "bad_number" });
  try {
    const numberId = await s.client.getNumberId(String(to));
    if (!numberId) return res.status(422).json({ error: "not_on_whatsapp" });
    const chatId = numberId._serialized;
    // Current WhatsApp Web stores the new message under another key (LID chats), so wwebjs often
    // returns undefined even though it was delivered. Capture the id from our own "message_create"
    // event instead. Django serializes sends (lock + rate limit), so the next outgoing one is ours.
    const wantType = pdf ? "document" : "chat";
    let captured = null;
    const onCreate = (m) => {
      if (!captured && m.fromMe && m.type === wantType) captured = m.id._serialized;
    };
    s.client.on("message_create", onCreate);
    let msg;
    try {
      const opts = { waitUntilMsgSent: true };
      if (pdf) {
        const media = new MessageMedia("application/pdf", pdf, filename || "document.pdf");
        msg = await s.client.sendMessage(chatId, media, { ...opts, caption: caption || "", sendMediaAsDocument: true });
      } else {
        msg = await s.client.sendMessage(chatId, caption || "", opts);
      }
      for (let i = 0; i < 20 && !(msg && msg.id) && !captured; i++) await new Promise((r) => setTimeout(r, 250));
    } finally {
      s.client.removeListener("message_create", onCreate);
    }
    const id = msg && msg.id ? msg.id._serialized : captured;
    // sendMessage didn't throw → WhatsApp accepted it. Without an id we just can't track ✓✓;
    // report success anyway so nobody retries and the customer doesn't get it twice.
    res.json({ id: id || null, tracked: Boolean(id) });
  } catch (err) {
    res.status(502).json({ error: "send_failed", detail: err.message });
  }
});

app.listen(PORT, "127.0.0.1", () => {
  console.log(`DigiClinic WhatsApp gateway on http://127.0.0.1:${PORT} (data: ${DATA_DIR})`);
  // Re-open sessions that were linked before, so a restart doesn't need a new QR.
  try {
    for (const dir of fs.readdirSync(DATA_DIR)) {
      const m = /^session-org-(.+)$/.exec(dir);
      if (m) start(m[1]);
    }
  } catch (_) {
    /* no saved sessions yet */
  }
});
