// Shared voice-playback module for سَند.
//
// Provides ONE control surface — window.SanadVoice — used by both the full
// assistant page (assistant.html) and the global floating widget
// (global_assistant.js), so "stop/replay/interrupt" behave identically
// everywhere.
//
// Strategy: try the server's neural TTS endpoint (/api/tts) first. If it
// reports {fallback:"browser"} (no provider available), or the network
// request itself fails, transparently fall back to the browser's built-in
// speechSynthesis — exactly the app's pre-existing behavior — so nothing
// ever visibly breaks even with zero server-side TTS configured.
(function () {
  let cachedVoiceSettings = null;
  let settingsFetchedAt = 0;
  const SETTINGS_TTL_MS = 30000; // re-fetch at most every 30s, or after an explicit save

  let currentAudio = null;          // the <audio> element for server-TTS playback
  let currentAudioUrl = null;       // the blob: URL backing currentAudio, so we can always revoke it
  let currentUtterance = null;      // the SpeechSynthesisUtterance for browser fallback
  let usingServerAudio = false;
  let lastSpokenText = "";
  let lastSpokenLang = "ar-SA";
  let onStateChange = null;         // optional callback(state) for UI (waveform/thinking indicators)

  // Monotonic call token: every speak()/stop() bumps this. An in-flight
  // speak() call checks its own captured token against the current one
  // after every await point, and discards its result (no playback, any
  // blob URL it already obtained gets revoked) if it's been superseded by
  // a newer speak()/stop() call. Without this, two speak() calls fired in
  // quick succession (e.g. a double-tap, or a fresh call arriving while a
  // previous network fetch is still resolving) could both end up setting
  // currentAudio and playing — overlapping/duplicate audio.
  let callToken = 0;

  function setState(state) {
    // state: "idle" | "speaking" | "paused"
    if (typeof onStateChange === "function") {
      try { onStateChange(state); } catch (e) { /* never let a UI callback break playback */ }
    }
  }

  async function fetchVoiceSettings(force) {
    const now = Date.now();
    if (!force && cachedVoiceSettings && (now - settingsFetchedAt) < SETTINGS_TTL_MS) {
      return cachedVoiceSettings;
    }
    try {
      const res = await fetch("/api/voice/settings", { credentials: "same-origin" });
      const data = await res.json();
      if (data && data.success) {
        cachedVoiceSettings = data.settings;
        settingsFetchedAt = now;
      }
    } catch (e) {
      // Keep whatever we had (or defaults below) — never block speaking on this.
    }
    return cachedVoiceSettings || { speed: 1.0, volume: 1.0, provider: "auto" };
  }

  function stopAll() {
    callToken++; // invalidate any in-flight speak() call immediately
    if (currentAudio) {
      try { currentAudio.pause(); currentAudio.currentTime = 0; } catch (e) { /* noop */ }
      currentAudio = null;
    }
    if (currentAudioUrl) {
      try { URL.revokeObjectURL(currentAudioUrl); } catch (e) { /* noop */ }
      currentAudioUrl = null;
    }
    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
    currentUtterance = null;
    usingServerAudio = false;
    setState("idle");
  }

  function pause() {
    if (usingServerAudio && currentAudio) {
      currentAudio.pause();
      setState("paused");
      return true;
    }
    if ("speechSynthesis" in window && window.speechSynthesis.speaking) {
      window.speechSynthesis.pause();
      setState("paused");
      return true;
    }
    return false;
  }

  function resume() {
    if (usingServerAudio && currentAudio) {
      currentAudio.play().catch(() => {});
      setState("speaking");
      return true;
    }
    if ("speechSynthesis" in window) {
      window.speechSynthesis.resume();
      setState("speaking");
      return true;
    }
    return false;
  }

  function speakWithBrowserVoice(text, lang, settings) {
    if (!("speechSynthesis" in window)) return false;
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = lang || "ar-SA";
    utter.rate = Math.max(0.5, Math.min(2.0, (settings && settings.speed) || 1.0));
    utter.volume = Math.max(0.0, Math.min(1.0, (settings && settings.volume) != null ? settings.volume : 1.0));
    utter.onstart = () => setState("speaking");
    utter.onend = () => setState("idle");
    utter.onerror = () => setState("idle");
    currentUtterance = utter;
    usingServerAudio = false;
    window.speechSynthesis.speak(utter);
    return true;
  }

  async function speak(text, options) {
    options = options || {};
    const lang = options.lang || (/[\u0600-\u06FF]/.test(text) ? "ar-SA" : "en-US");
    if (!text || !text.trim()) return;

    stopAll(); // bumps callToken, stops+releases whatever was playing before
    const myToken = callToken;
    lastSpokenText = text;
    lastSpokenLang = lang;

    const settings = await fetchVoiceSettings(false);
    if (myToken !== callToken) return; // superseded while awaiting settings

    // If the user explicitly chose "browser" mode, skip the network round
    // trip entirely and go straight to speechSynthesis.
    if (settings.provider === "browser") {
      speakWithBrowserVoice(text, lang, settings);
      return;
    }

    try {
      const res = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ text, lang: lang.slice(0, 2) }),
      });
      if (myToken !== callToken) return; // superseded during the network round trip

      const contentType = res.headers.get("Content-Type") || "";
      if (res.ok && contentType.indexOf("audio") !== -1) {
        const blob = await res.blob();
        if (myToken !== callToken) return; // superseded while reading the body

        const url = URL.createObjectURL(blob);
        if (myToken !== callToken) { URL.revokeObjectURL(url); return; } // superseded right after creating the URL

        const audio = new Audio(url);
        audio.volume = Math.max(0.0, Math.min(1.0, settings.volume != null ? settings.volume : 1.0));
        audio.onplay = () => setState("speaking");
        audio.onended = () => {
          setState("idle");
          URL.revokeObjectURL(url);
          if (currentAudioUrl === url) currentAudioUrl = null;
        };
        audio.onerror = () => {
          setState("idle");
          URL.revokeObjectURL(url);
          if (currentAudioUrl === url) currentAudioUrl = null;
        };
        currentAudio = audio;
        currentAudioUrl = url;
        usingServerAudio = true;
        await audio.play();
        return;
      }
    } catch (e) {
      // Network error, provider down, etc. — fall through to browser voice.
    }

    if (myToken !== callToken) return; // superseded while we were falling through
    // Server TTS unavailable for any reason -> transparent fallback.
    speakWithBrowserVoice(text, lang, settings);
  }

  function replayLast() {
    if (!lastSpokenText) return;
    speak(lastSpokenText, { lang: lastSpokenLang });
  }

  function isSpeaking() {
    if (usingServerAudio && currentAudio) return !currentAudio.paused;
    if ("speechSynthesis" in window) return window.speechSynthesis.speaking;
    return false;
  }

  async function saveSettings(updates) {
    const res = await fetch("/api/voice/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(updates),
    });
    const data = await res.json();
    if (data && data.success) {
      cachedVoiceSettings = data.settings;
      settingsFetchedAt = Date.now();
    }
    return data;
  }

  window.SanadVoice = {
    speak,
    stop: stopAll,
    pause,
    resume,
    replayLast,
    isSpeaking,
    getSettings: () => fetchVoiceSettings(false),
    refreshSettings: () => fetchVoiceSettings(true),
    saveSettings,
    onStateChange: (cb) => { onStateChange = cb; },
  };
})();
