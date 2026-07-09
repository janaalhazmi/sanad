// Proves: when /api/tts responds with the {"fallback":"browser"} JSON
// signal (content-type application/json, not audio) — the real, current
// state of this sandbox (no edge-tts/openai installed) — voice_player.js
// correctly falls through to speechSynthesis, with no console errors and
// no unhandled exceptions.
const fs = require("fs");
const vm = require("vm");

const results = [];
function check(name, cond, detail) {
  results.push([name, cond]);
  console.log((cond ? "PASS" : "FAIL") + "  " + name + (detail ? "  " + detail : ""));
}

let consoleErrors = [];
const fakeConsole = {
  log: () => {},
  error: (...args) => consoleErrors.push(args.join(" ")),
  warn: () => {},
};

global.URL = {
  createObjectURL: () => "blob:should-not-be-called",
  revokeObjectURL: () => {},
};
global.Audio = class { constructor() { throw new Error("Audio() should never be constructed on the fallback path"); } };

global.fetch = function (url, opts) {
  if (url === "/api/voice/settings") {
    return Promise.resolve({
      ok: true,
      headers: { get: () => "application/json" },
      json: () => Promise.resolve({ success: true, settings: { speed: 1.0, volume: 1.0, provider: "auto", gender: "male" } }),
    });
  }
  if (url === "/api/tts") {
    // Exactly what the real server returns right now in this sandbox.
    return Promise.resolve({
      ok: true,
      headers: { get: () => "application/json" },
      json: () => Promise.resolve({ success: false, fallback: "browser" }),
    });
  }
  return Promise.reject(new Error("unexpected fetch: " + url));
};

let spokenUtterances = [];
global.window = {
  speechSynthesis: {
    speaking: false,
    cancel() {},
    pause() {},
    resume() {},
    speak(utter) {
      spokenUtterances.push(utter);
      if (utter.onstart) utter.onstart();
    },
  },
};
global.SpeechSynthesisUtterance = function (text) {
  this.text = text;
  this.lang = "";
  this.rate = 1;
  this.volume = 1;
  this.onstart = null;
  this.onend = null;
  this.onerror = null;
};

const code = fs.readFileSync("/home/claude/sanad/static/js/voice_player.js", "utf8");
const context = {
  window: global.window, URL: global.URL, Audio: global.Audio,
  fetch: global.fetch, SpeechSynthesisUtterance: global.SpeechSynthesisUtterance,
  console: fakeConsole,
};
vm.createContext(context);

let threw = null;
try {
  vm.runInContext(code, context);
} catch (e) {
  threw = e;
}
check("voice_player.js loads without throwing", threw === null, threw && threw.message);

const SanadVoice = context.window.SanadVoice;

async function settle() {
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
}

async function main() {
  let speakThrew = null;
  try {
    await SanadVoice.speak("مرحباً بك في سَند", { lang: "ar-SA" });
  } catch (e) {
    speakThrew = e;
  }
  await settle();

  check("speak() with server fallback signal does not throw", speakThrew === null, speakThrew && speakThrew.message);
  check("speak() falls back to browser speechSynthesis (utterance spoken)", spokenUtterances.length === 1, `count=${spokenUtterances.length}`);
  check("the spoken text matches what was requested", spokenUtterances[0] && spokenUtterances[0].text === "مرحباً بك في سَند");
  check("no console.error was ever triggered", consoleErrors.length === 0, consoleErrors.join(" | "));

  console.log("\n=== SUMMARY ===");
  const ok = results.every(([, c]) => c);
  console.log(ok ? "ALL PASS" : "SOME FAILED");
  process.exit(ok ? 0 : 1);
}

main();
