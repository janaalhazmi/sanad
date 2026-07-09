// Shared helpers for the سَند app

async function apiFetch(url, options = {}) {
  const opts = {
    method: "GET",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...options,
  };
  if (opts.body && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
  }
  try {
    const res = await fetch(url, opts);
    let data = {};
    try {
      data = await res.json();
    } catch (e) {
      data = {};
    }
    return { ok: res.ok, status: res.status, data };
  } catch (err) {
    console.error("Network error calling", url, err);
    return { ok: false, status: 0, data: { success: false, message: "تعذر الاتصال بالخادم" } };
  }
}

function showToast(message, type = "") {
  let toast = document.querySelector(".toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.className = "toast";
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.className = "toast show" + (type ? " " + type : "");
  clearTimeout(toast._hideTimer);
  toast._hideTimer = setTimeout(() => {
    toast.classList.remove("show");
  }, 3200);
}

function logout() {
  apiFetch("/api/logout", { method: "POST" }).finally(() => {
    window.location.href = "/";
  });
}

// ---------------------------------------------------------------------
// Shared SpeechRecognition error mapping — used by every mic entry point
// (assistant page inline mic, global floating mic widget) so a real
// recognition failure always surfaces its ACTUAL cause instead of one
// generic "تعذر التعرف على الصوت" message. The raw event is also always
// logged to the console (error/message/timeStamp) so it's inspectable
// during debugging, never silently swallowed.
// ---------------------------------------------------------------------
function speechRecognitionErrorMessage(event) {
  const code = (event && event.error) || "unknown";
  console.error("SpeechRecognition error:", code, event);

  const MESSAGES = {
    "not-allowed": "يرجى السماح باستخدام الميكروفون.",
    "permission-denied": "يرجى السماح باستخدام الميكروفون.",
    "no-speech": "لم يتم اكتشاف أي كلام.",
    "audio-capture": "لم يتم العثور على ميكروفون.",
    "network": "حدث خطأ في خدمة التعرف على الصوت.",
    "aborted": "تم إيقاف الاستماع.",
    "service-not-allowed": "خدمة التعرف على الصوت غير متاحة حالياً.",
    "language-not-supported": "اللغة العربية غير مدعومة على هذا المتصفح للتعرف على الصوت.",
    "bad-grammar": "تعذر فهم الصوت المسجل.",
  };

  return MESSAGES[code] || `تعذر التعرف على الصوت (${code})`;
}

// True once we've actually gotten a definitive permission answer for the
// microphone (granted/denied) via the Permissions API, where supported.
// Purely informational — recognition.start() still triggers the browser's
// own prompt when needed; this just lets us give a clearer message
// up-front instead of waiting for a generic 'not-allowed' error.
async function getMicPermissionState() {
  try {
    if (!navigator.permissions || !navigator.permissions.query) return "unknown";
    const status = await navigator.permissions.query({ name: "microphone" });
    return status.state; // "granted" | "denied" | "prompt"
  } catch (e) {
    return "unknown";
  }
}

// ---------------------------------------------------------------------
// Unified mic controller — the ONE place that decides between native
// browser SpeechRecognition and the server-side Whisper fallback
// (stt_fallback.js), and logs every lifecycle stage so a silent failure
// is always visible in the console instead of just "didn't work".
//
// Automatically falls back to server-side Whisper when:
//   - there is no native SpeechRecognition/webkitSpeechRecognition at all
//     (e.g. Firefox), or the page isn't a secure context (native speech
//     APIs require HTTPS or localhost and otherwise silently misbehave);
//   - the native engine reports an error that means ITS OWN service is
//     unavailable right now (network / service-not-allowed / audio-capture
//     / language-not-supported) — not just "no speech detected";
//   - onstart never fires within a few seconds of calling start() (some
//     browsers/origins silently swallow the call instead of erroring).
//
// Guards against the exact bug that used to cause an immediate, bogus
// "تم إيقاف الاستماع" right after pressing the mic: toggle() is async (it
// awaits a mic-permission check before calling recognition.start()), so a
// second click/call arriving while the first was still awaiting used to
// race a second start() on the SAME recognition instance — which threw,
// and the resulting fallback path called recognition.abort(), aborting
// the FIRST (legitimate) session the instant it had just started. A
// synchronous `starting` guard now makes any such re-entrant call a no-op
// instead. Also pauses the wake-word background listener (if any) for the
// duration of a manual/command session, since two concurrent
// SpeechRecognition sessions is exactly what makes a browser abort one.
//
// options: { lang: () => string, onTranscript: (text) => void,
//            onListeningChange: (bool) => void, onStatus: (msg, type) => void }
// returns: { toggle: async () => void }
// ---------------------------------------------------------------------
function createMicController(options) {
  const getLang = options.lang || (() => "ar-SA");
  const onTranscript = options.onTranscript || function () {};
  const onListeningChange = options.onListeningChange || function () {};
  const onStatus = options.onStatus || function (msg, type) { showToast(msg, type); };

  const SpeechRecognitionImpl = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognition = null;
  let listening = false;
  let starting = false;          // true from the moment toggle() decides to start until onstart/onerror/onend settles it
  let userInitiatedStop = false; // true only while recognition.stop() was called because the USER clicked to stop
  let fallbackHandle = null;
  let startWatchdog = null;
  let didStart = false;

  function setListening(v) {
    listening = v;
    onListeningChange(v);
  }

  function clearWatchdog() {
    if (startWatchdog) { clearTimeout(startWatchdog); startWatchdog = null; }
  }

  function resumeWakeWord() {
    if (window.SanadWakeWord && window.SanadWakeWord.resumeAfterCommand) {
      window.SanadWakeWord.resumeAfterCommand();
    }
  }

  async function startFallbackRecording() {
    if (!window.SanadSTTFallback) {
      onStatus("التعرف على الصوت غير مدعوم في هذا المتصفح", "error");
      return;
    }
    try {
      fallbackHandle = await window.SanadSTTFallback.recordAndTranscribe(getLang());
      setListening(true);
      onStatus("جارِ التسجيل... اضغط الميكروفون مرة أخرى لإرسال ما قلته", "");
    } catch (e) {
      console.error("Server-side STT fallback failed to start recording:", e);
      onStatus("تعذر الوصول إلى الميكروفون: " + (e && e.message ? e.message : e), "error");
      setListening(false);
      resumeWakeWord();
    }
  }

  async function stopFallbackRecording() {
    if (!fallbackHandle) { setListening(false); resumeWakeWord(); return; }
    const handle = fallbackHandle;
    fallbackHandle = null;
    onStatus("جارِ التعرف على الصوت...", "");
    try {
      const text = await handle.stopAndTranscribe();
      setListening(false);
      resumeWakeWord();
      if (text && text.trim()) {
        onTranscript(text.trim());
      } else {
        onStatus("لم يتم التعرف على أي كلام", "error");
      }
    } catch (e) {
      console.error("Server-side STT transcription failed:", e);
      setListening(false);
      resumeWakeWord();
      onStatus(e && e.message ? e.message : "تعذر التعرف على الصوت عبر الخادم", "error");
    }
  }

  function fallbackToServerSTT(reason) {
    console.warn("Native SpeechRecognition failed/unavailable — falling back to server-side Whisper. Reason:", reason);
    starting = false;
    if (recognition) {
      try { recognition.abort(); } catch (e) { /* ignore */ }
    }
    clearWatchdog();
    setListening(false);
    startFallbackRecording();
  }

  const nativeUsable = !!SpeechRecognitionImpl && window.isSecureContext !== false;

  if (!nativeUsable) {
    console.info(
      SpeechRecognitionImpl
        ? "Page is not a secure context (needs HTTPS or localhost) — using server-side Whisper for voice input."
        : "No native SpeechRecognition in this browser — using server-side Whisper for voice input."
    );
    return {
      toggle: async () => {
        if (listening) { await stopFallbackRecording(); } else { await startFallbackRecording(); }
      },
    };
  }

  recognition = new SpeechRecognitionImpl();
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;
  recognition.lang = getLang();

  recognition.onstart = () => {
    didStart = true;
    starting = false;
    clearWatchdog();
    console.log("SpeechRecognition: onstart fired");
  };
  recognition.onaudiostart = () => console.log("SpeechRecognition: onaudiostart fired");
  recognition.onspeechstart = () => console.log("SpeechRecognition: onspeechstart fired (speech detected)");
  recognition.onspeechend = () => console.log("SpeechRecognition: onspeechend fired");
  recognition.onaudioend = () => console.log("SpeechRecognition: onaudioend fired");
  recognition.onnomatch = (event) => {
    console.warn("SpeechRecognition: onnomatch fired", event);
    onStatus("لم يتم التعرف على أي كلمات، حاول مرة أخرى", "error");
  };
  recognition.onresult = (event) => {
    console.log("SpeechRecognition: onresult fired", event.results);
    const result = event.results && event.results[0] && event.results[0][0];
    if (!result || !result.transcript || !result.transcript.trim()) {
      console.warn("SpeechRecognition: onresult fired but transcript was empty", event);
      onStatus("لم يتم التعرف على أي كلمات، حاول مرة أخرى", "error");
      return;
    }
    onTranscript(result.transcript.trim());
  };
  recognition.onerror = (event) => {
    clearWatchdog();
    starting = false;
    const code = event && event.error;
    const wasUserStop = userInitiatedStop;
    userInitiatedStop = false;
    setListening(false);
    console.error("SpeechRecognition: onerror fired:", code, event, "wasUserStop=", wasUserStop);

    if (code === "aborted" && wasUserStop) {
      // The user themselves clicked to stop — this IS the expected,
      // intentional "تم إيقاف الاستماع" case.
      resumeWakeWord();
      onStatus("تم إيقاف الاستماع.", "");
      return;
    }
    if (code === "aborted") {
      // Aborted for some OTHER reason (a stray duplicate start() call, a
      // competing recognition session, etc) — NOT something the user did.
      // Showing "تم إيقاف الاستماع" here would be misleading (the user
      // never clicked stop), so just ask them to try again instead.
      console.warn("SpeechRecognition aborted for a reason other than a user-initiated stop.");
      resumeWakeWord();
      onStatus("تعذر بدء الاستماع، حاول الضغط على الميكروفون مرة أخرى.", "error");
      return;
    }
    // These specific codes mean the BROWSER's own recognition service is
    // unavailable right now (no network path to it, blocked, no mic, or
    // the requested language isn't supported by it) — retry immediately
    // through server-side Whisper instead of just reporting an error.
    if (["network", "service-not-allowed", "audio-capture", "language-not-supported"].indexOf(code) !== -1) {
      fallbackToServerSTT("onerror:" + code);
      return;
    }
    resumeWakeWord();
    onStatus(speechRecognitionErrorMessage(event), "error");
  };
  recognition.onend = () => {
    clearWatchdog();
    starting = false;
    setListening(false);
    resumeWakeWord();
    console.log("SpeechRecognition: onend fired (didStart=" + didStart + ")");
  };

  return {
    toggle: async () => {
      if (listening) {
        // Explicit user-initiated stop — the ONLY case that should ever
        // surface "تم إيقاف الاستماع".
        userInitiatedStop = true;
        recognition.stop();
        return;
      }

      // Re-entrancy guard: ignore a duplicate/overlapping call that
      // arrives while a previous click is still awaiting the permission
      // check below, so we can never call start() twice on the same
      // recognition instance (see the block comment above this function
      // for why that used to cause a bogus immediate "stopped" message).
      if (starting) {
        console.log("SpeechRecognition: toggle() ignored — already starting from a previous click.");
        return;
      }
      starting = true;

      // Pause the wake-word background listener (if active) so it can
      // never compete with this manual/command session for the
      // microphone — two concurrent SpeechRecognition sessions is
      // exactly what causes a browser to abort one of them.
      if (window.SanadWakeWord && window.SanadWakeWord.pauseForCommand) {
        window.SanadWakeWord.pauseForCommand();
      }

      const permission = await getMicPermissionState();
      if (permission === "denied") {
        starting = false;
        resumeWakeWord();
        onStatus("يرجى السماح باستخدام الميكروفون.", "error");
        return;
      }
      // Something else (e.g. the user clicking stop, or another call)
      // may have changed state while we were awaiting the permission
      // check above — don't proceed with a stale start() in that case.
      if (listening || !starting) {
        return;
      }

      didStart = false;
      recognition.lang = getLang();
      try {
        console.log("SpeechRecognition: calling start()...");
        recognition.start();
        setListening(true);
        // Watchdog: some browsers/origins silently swallow start() with
        // no error and no onstart ever firing — after a few seconds with
        // nothing happening, treat that as a failure and use server-side
        // Whisper instead of leaving the mic stuck in "listening" forever.
        startWatchdog = setTimeout(() => {
          if (!didStart) {
            console.warn("SpeechRecognition: onstart never fired within 4s of start() — treating as failed.");
            fallbackToServerSTT("start-timeout");
          }
        }, 4000);
      } catch (e) {
        starting = false;
        console.error("SpeechRecognition: start() threw synchronously:", e);
        fallbackToServerSTT("start-threw:" + (e && e.message));
      }
    },
  };
}
