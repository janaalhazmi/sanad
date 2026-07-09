// Client-side Accessibility module for سَند.
//
// Deliberately thin: all sizing/contrast lives in CSS (.senior-mode /
// .high-contrast body classes, already applied server-side at render time
// via base.html + the Flask context processor — this module's job is (1)
// applying those classes INSTANTLY when changed on the Accessibility page
// itself, without waiting for a reload, and (2) wiring the "read X aloud"
// behaviors on top of the EXISTING voice pipeline: window.SanadVoice
// (Step 3's TTS) and window.SanadGlobalAssistant.readScreen (Step 1's
// screen reader). No new speech synthesis code lives here.
(function () {
  let cachedSettings = window.SANAD_ACCESSIBILITY || {
    senior_mode: false, high_contrast: false, auto_read_screen: false,
    read_notifications: true, read_errors: true, read_success: true,
    read_balances: true, read_transactions: true, read_otp_instructions: true,
  };

  function applyBodyClasses(settings) {
    document.body.classList.toggle("senior-mode", !!settings.senior_mode);
    document.body.classList.toggle("high-contrast", !!settings.high_contrast);
  }

  // Apply immediately on script load using whatever the server already
  // rendered (this is normally a no-op re-confirmation of the same classes
  // base.html already set — but becomes load-bearing right after a
  // settings change on the current page, before any navigation happens).
  applyBodyClasses(cachedSettings);

  function speakIfEnabled(text, category) {
    if (!text || !text.trim()) return;
    if (!cachedSettings.senior_mode) return;
    const categoryFlag = {
      notification: "read_notifications",
      error: "read_errors",
      success: "read_success",
      balance: "read_balances",
      transaction: "read_transactions",
      otp: "read_otp_instructions",
    }[category];
    if (categoryFlag && cachedSettings[categoryFlag] === false) return;
    if (window.SanadVoice) {
      window.SanadVoice.speak(text, { lang: /[\u0600-\u06FF]/.test(text) ? "ar-SA" : "en-US" });
    }
  }

  // ------------------------------------------------------------------
  // Hook 1: auto-read the whole screen on page load (reuses Step 1's
  // existing DOM-walking reader — no duplicate logic).
  // ------------------------------------------------------------------
  function autoReadScreenOnLoad() {
    if (!cachedSettings.senior_mode || !cachedSettings.auto_read_screen) return;
    if (!window.SanadGlobalAssistant) return;
    // Small delay so the page has finished rendering (skeletons resolved,
    // dynamic content populated) before the sweep reads it.
    setTimeout(() => {
      try {
        window.SanadGlobalAssistant.readScreen();
      } catch (e) {
        // Never let auto-read break the page.
      }
    }, 700);
  }

  // ------------------------------------------------------------------
  // Hook 2: read toast messages (errors/success) app-wide. main.js's
  // showToast() is the single choke point virtually every error/success
  // message in the app already flows through, so wrapping it here covers
  // the whole app without touching every call site.
  // ------------------------------------------------------------------
  function wrapShowToast() {
    if (typeof window.showToast !== "function" || window.showToast._a11yWrapped) return;
    const original = window.showToast;
    function wrapped(message, type) {
      original(message, type);
      if (type === "error") speakIfEnabled(message, "error");
      else if (type === "success") speakIfEnabled(message, "success");
    }
    wrapped._a11yWrapped = true;
    window.showToast = wrapped;
  }

  // ------------------------------------------------------------------
  // Public API
  // ------------------------------------------------------------------
  function applySettings(settings) {
    cachedSettings = settings;
    applyBodyClasses(settings);
  }

  async function refreshFromServer() {
    try {
      const res = await fetch("/api/accessibility/settings", { credentials: "same-origin" });
      const data = await res.json();
      if (data && data.success) applySettings(data.settings);
    } catch (e) {
      // Keep whatever we had — never block the page on this.
    }
    return cachedSettings;
  }

  async function saveSettings(updates) {
    const res = await fetch("/api/accessibility/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(updates),
    });
    const data = await res.json();
    if (data && data.success) applySettings(data.settings);
    return data;
  }

  window.SanadAccessibility = {
    getSettings: () => cachedSettings,
    applySettings,
    refreshFromServer,
    saveSettings,
    speak: speakIfEnabled, // for page-specific hooks (e.g. auth_verify.html's OTP instructions)
  };

  function init() {
    wrapShowToast();
    autoReadScreenOnLoad();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
