/*
 * DigiClinic voice dictation (docs/plan/13-voice-dictation.md).
 *
 * Any <textarea>/<input> with `data-dictate` gets a 🎤 button (+ ع/EN toggle). Click (or Ctrl+Space in the field)
 * starts; a second click or 3 s of silence stops. Interim text shows grey under the field; final text is inserted
 * at the cursor (never replaces what's written) and fires `input`, so the field's autosave runs.
 * Spoken commands: "سطر جديد" → new line, "نقطة" → ".".
 *
 * `data-dictate="rx"` (prescription): finals are collected instead of inserted; on stop the text is POSTed to
 * `data-dictate-url` and the server's line-by-line drug suggestions are shown in `data-dictate-target`.
 *
 * Providers implement: isSupported(), start(lang), stop(), onInterim(cb), onFinal(cb), onError(cb), onEnd(cb).
 * v1 = WebSpeechProvider (Chrome/Edge). A WhisperProvider (MediaRecorder → /api/transcribe/) can be added later
 * without touching the UI code.
 */
(() => {
  "use strict";

  class WebSpeechProvider {
    static isSupported() {
      return Boolean(window.SpeechRecognition || window.webkitSpeechRecognition) && window.isSecureContext !== false;
    }

    constructor() {
      const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      this.rec = new Recognition();
      this.rec.continuous = true;
      this.rec.interimResults = true;
      this.handlers = { interim: () => {}, final: () => {}, error: () => {}, end: () => {} };
      this.rec.onresult = (event) => {
        let interim = "";
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const result = event.results[i];
          if (result.isFinal) this.handlers.final(result[0].transcript);
          else interim += result[0].transcript;
        }
        this.handlers.interim(interim);
      };
      this.rec.onerror = (event) => this.handlers.error(event.error || "error");
      this.rec.onend = () => this.handlers.end();
    }

    start(lang) { this.rec.lang = lang; this.rec.start(); }
    stop() { try { this.rec.stop(); } catch (e) { /* already stopped */ } }
    onInterim(cb) { this.handlers.interim = cb; }
    onFinal(cb) { this.handlers.final = cb; }
    onError(cb) { this.handlers.error = cb; }
    onEnd(cb) { this.handlers.end = cb; }
  }

  const Provider = WebSpeechProvider;
  const SILENCE_MS = 3000;
  const ERRORS = {
    "not-allowed": "المايك مقفول — اسمح للمتصفح يستخدم المايك.",
    "service-not-allowed": "المايك مقفول — اسمح للمتصفح يستخدم المايك.",
    network: "الإملاء الصوتي محتاج إنترنت.",
    "audio-capture": "مفيش مايك متوصل.",
  };

  function applyCommands(text) {
    return text
      .replace(/\s*سطر جديد\s*/g, "\n")
      .replace(/\s*(نقطة|نقطه)(?=\s|$)/g, ".")
      .replace(/[ \t]+/g, " ")
      .trim();
  }

  function insertAtCursor(el, text) {
    if (!text) return;
    const start = el.selectionStart ?? el.value.length;
    const end = el.selectionEnd ?? el.value.length;
    const before = el.value.slice(0, start);
    const needsSpace = before && !/\s$/.test(before) && !/^[\n.،,]/.test(text);
    el.setRangeText((needsSpace ? " " : "") + text, start, end, "end");
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }

  let active = null; // the running session (one at a time)

  function langPref() {
    try { return localStorage.getItem("dictation-lang") || "ar-EG"; } catch (e) { return "ar-EG"; }
  }

  function attach(el) {
    if (el.dataset.dictateReady) return;
    el.dataset.dictateReady = "1";
    const bar = document.createElement("span");
    bar.className = "dictation-bar";
    bar.style.cssText = "display:inline-flex;gap:4px;align-items:center;margin-top:4px";
    const mic = document.createElement("button");
    mic.type = "button";
    mic.className = "dictation-mic";
    mic.title = "إملاء صوتي (Ctrl+Space)";
    mic.setAttribute("aria-label", "إملاء صوتي");
    mic.textContent = "🎤";
    mic.style.cssText = "border:1px solid #d6d3d1;border-radius:9999px;padding:2px 8px;background:#fff;font-size:13px";
    const lang = document.createElement("button");
    lang.type = "button";
    lang.className = "dictation-lang";
    lang.style.cssText = "font-size:11px;color:#78716c;padding:2px 4px";
    const renderLang = () => { lang.textContent = langPref() === "ar-EG" ? "ع" : "EN"; };
    renderLang();
    lang.title = "تبديل لغة الإملاء";
    lang.addEventListener("click", () => {
      try { localStorage.setItem("dictation-lang", langPref() === "ar-EG" ? "en-US" : "ar-EG"); } catch (e) { /* ignore */ }
      document.querySelectorAll(".dictation-lang").forEach((b) => { b.textContent = langPref() === "ar-EG" ? "ع" : "EN"; });
    });
    const interim = document.createElement("span");
    interim.className = "dictation-interim";
    interim.style.cssText = "color:#a8a29e;font-size:12px";
    bar.append(mic, lang, interim);
    el.insertAdjacentElement("afterend", bar);
    mic.addEventListener("click", () => toggle(el, mic, interim));
  }

  function toggle(el, mic, interimEl) {
    if (active && active.el === el) { active.stop(); return; }
    if (active) active.stop();
    start(el, mic, interimEl);
  }

  function start(el, mic, interimEl) {
    const provider = new Provider();
    const collected = [];
    let silence = null;
    const resetSilence = () => {
      clearTimeout(silence);
      silence = setTimeout(() => session.stop(), SILENCE_MS);
    };
    const session = {
      el,
      stopped: false,
      stop() {
        if (this.stopped) return;
        this.stopped = true;
        clearTimeout(silence);
        provider.stop();
        finish();
      },
    };
    const finish = () => {
      mic.style.background = "#fff";
      mic.style.color = "";
      interimEl.textContent = "";
      if (active === session) active = null;
      if (el.dataset.dictate === "rx" && collected.length && window.htmx) {
        window.htmx.ajax("POST", el.dataset.dictateUrl, {
          target: el.dataset.dictateTarget || "#rx-dictation",
          values: { text: collected.join("\n") },
        });
      }
    };
    provider.onInterim((text) => { interimEl.textContent = text; resetSilence(); });
    provider.onFinal((text) => {
      resetSilence();
      const clean = applyCommands(text);
      if (el.dataset.dictate === "rx") collected.push(clean); // each pause = a new prescription line
      else insertAtCursor(el, clean);
    });
    provider.onError((code) => {
      if (code !== "no-speech" && code !== "aborted") interimEl.textContent = ERRORS[code] || "حصلت مشكلة في الإملاء.";
      session.stop();
    });
    provider.onEnd(() => session.stop());
    active = session;
    mic.style.background = "#dc2626";
    mic.style.color = "#fff";
    el.focus();
    try {
      provider.start(langPref());
      resetSilence();
    } catch (e) {
      session.stop();
    }
  }

  function scan(root) {
    if (!Provider.isSupported()) {
      (root.querySelectorAll ? root : document).querySelectorAll("[data-dictation-unsupported]").forEach((n) => {
        n.hidden = false;
      });
      return;
    }
    (root.querySelectorAll ? root : document).querySelectorAll("[data-dictate]").forEach(attach);
  }

  document.addEventListener("keydown", (e) => {
    if (!(e.ctrlKey && e.code === "Space")) return;
    const el = document.activeElement;
    if (!el || !el.dataset || el.dataset.dictateReady !== "1") return;
    e.preventDefault();
    const bar = el.nextElementSibling;
    if (bar && bar.classList.contains("dictation-bar")) bar.querySelector(".dictation-mic").click();
  });

  document.addEventListener("DOMContentLoaded", () => scan(document));
  document.addEventListener("htmx:afterSettle", (e) => scan(e.target));

  window.DigiDictation = { providers: { webspeech: WebSpeechProvider }, applyCommands, insertAtCursor, scan, attach };
  if (document.readyState !== "loading") scan(document);
})();
