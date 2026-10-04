/*
 * Workaround for whatsapp-web.js 1.34.x on WhatsApp Web 2.3000.10477+ (since 2026-09-17):
 * media sends fail with "Data passed to getter must include an id property (it's how we memoize)".
 * sendMessage spreads mediaOptions (including its private __x_id: undefined) into the outgoing
 * message, which clobbers the real id. We delete it right after the object is built.
 * Idempotent; runs on npm postinstall and on every gateway start. Refs: wwebjs issues #201921/#201922.
 */
"use strict";
const fs = require("fs");
const path = require("path");

const FILE = path.join(__dirname, "node_modules", "whatsapp-web.js", "src", "util", "Injected", "Utils.js");
const ANCHOR = "// Bot's won't reply if canonicalUrl is set (linking)";
const FIX = "delete message.__x_id; // marsool: media id fix";

function patch() {
  let src;
  try {
    src = fs.readFileSync(FILE, "utf8");
  } catch (_) {
    return "missing";
  }
  if (src.includes(FIX)) return "already";
  if (!src.includes(ANCHOR)) return "anchor-not-found";
  fs.writeFileSync(FILE, src.replace(ANCHOR, `${FIX}\n        ${ANCHOR}`));
  return "patched";
}

module.exports = patch;
if (require.main === module) console.log("wwebjs media fix:", patch());
