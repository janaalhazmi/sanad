// Wake Word module for سَند — "Hey Sanad" / "يا سند".
//
// Entirely a LISTENING layer: once the wake phrase is detected, it simply
// clicks the existing global assistant mic button (global_assistant.js),
// which starts the exact same single-command recognition + /api/assistant
// flow already used everywhere else. No command parsing, intent
// detection, or speech synthesis logic is duplicated here.
//
// Continuous background speech recognition is inherently a bit fragile
// across browsers (auto-stops after a period of silence, competes with
// any other active recognition instance, requires mic permission already
// granted) — this module is defensive about restarting itself and never
// throws in a way that could break the rest of the page.
(function () {
  const WAKE_PHRASES_NORMALIZED = ["hey sanad", "hey sanaد", "يا سند", "ياسند", "هاي سند"];

  let recognition = null;
  let enabled = false;
  let listening = false;
  let restartTimer = null;

  function normalize(text) {
    return (text || "")
      .toLowerCase()
      .replace(/[إأآ]/g, "ا")
      .replace(/ى/g, "ي")
      .replace(/[.,!?؟،]/g, "")
      .trim();
  }

  function containsWakePhrase(transcript) {
    const norm = normalize(transcript);
    return WAKE_PHRASES_NORMALIZED.some((phrase) => norm.includes(phrase));
  }

  function triggerCommandCapture() {
    const micBtn = document.getElementById("globalAssistantMic");
    if (!micBtn) return;
    // Stop the wake-word listener first so it doesn't compete with the
    // command recognizer for the microphone.
    stopListening(/* keepEnabled */ true);
    if (typeof showToast === "function") showToast("نعم، أنا أستمع...", "");
    setTimeout(() => micBtn.click(), 350);
  }

  function scheduleRestart() {
    if (!enabled) return;
    clearTimeout(restartTimer);
    restartTimer = setTimeout(() => startListening(), 800);
  }

  function startListening() {
    if (!enabled || listening) return;
    const SpeechRecognitionImpl = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognitionImpl) return;
    if (!document.getElementById("globalAssistantMic")) return; // widget not on this page

    try {
      recognition = new SpeechRecognitionImpl();
      recognition.lang = "ar-SA";
      recognition.continuous = true;
      recognition.interimResults = true;

      recognition.onresult = (event) => {
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const transcript = event.results[i][0].transcript;
          if (containsWakePhrase(transcript)) {
            triggerCommandCapture();
            return;
          }
        }
      };
      recognition.onerror = () => {
        listening = false;
        scheduleRestart();
      };
      recognition.onend = () => {
        listening = false;
        scheduleRestart(); // browsers stop continuous recognition periodically; keep it alive
      };

      recognition.start();
      listening = true;
    } catch (e) {
      listening = false;
      scheduleRestart();
    }
  }

  function stopListening(keepEnabled) {
    clearTimeout(restartTimer);
    if (recognition) {
      try { recognition.onend = null; recognition.stop(); } catch (e) { /* noop */ }
      recognition = null;
    }
    listening = false;
    if (!keepEnabled) enabled = false;
  }

  function setEnabled(value) {
    enabled = !!value;
    if (enabled) startListening();
    else stopListening();
  }

  window.SanadWakeWord = {
    setEnabled,
    isEnabled: () => enabled,
    // Used by main.js's createMicController so a manual/command mic
    // session never has to compete with this background listener for the
    // microphone (two concurrent SpeechRecognition sessions is exactly
    // what causes a browser to abort one of them). Does NOT change the
    // user's wake-word setting — just pauses/resumes the listener itself.
    pauseForCommand: () => stopListening(/* keepEnabled */ true),
    resumeAfterCommand: () => { if (enabled) scheduleRestart(); },
  };

  function init() {
    if (window.SANAD_WAKE_WORD_ENABLED) {
      // Small delay so the global assistant widget's own init() (which
      // creates #globalAssistantMic and its recognition instance) has
      // definitely run first.
      setTimeout(() => setEnabled(true), 500);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
