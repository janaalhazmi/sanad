// Global assistant widget — floating mic + "read screen" button, available
// on every authenticated page. Reads content dynamically from the DOM
// (never hardcoded), sends voice/text to /api/assistant with the current
// page context, and can navigate/read/go-back/close/logout based on the
// assistant's response.

(function () {
  function currentPageLabel() {
    const map = {
      dashboard: "الصفحة الرئيسية", cards: "البطاقات", transfer: "صفحة التحويل",
      beneficiaries: "صفحة المستفيدين", notifications: "الإشعارات",
      transactions: "كشف الحساب", settings: "الإعدادات", assistant: "المساعد",
    };
    return map[window.SANAD_PAGE] || "هذه الصفحة";
  }

  // ------------------------------------------------------------------
  // Read Screen: walk the visible DOM in natural top-to-bottom reading
  // order and speak only meaningful page content — headings, labels,
  // balances, buttons, form fields/values, and transaction rows. Chat
  // bubbles, hidden elements, decorative/icon-only nodes, duplicate text,
  // and technical/UI chrome (IDs, classes, nav rail, the assistant widget
  // itself) are all excluded. Nothing here is hardcoded per-page — it's a
  // generic content walk that works on every screen.
  // ------------------------------------------------------------------
  function isVisible(el) {
    if (!el || el.nodeType !== 1) return false;
    if (el.hasAttribute("aria-hidden") && el.getAttribute("aria-hidden") !== "false") return false;
    if (el.hasAttribute("hidden")) return false;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || parseFloat(style.opacity) === 0) return false;
    return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  }

  // Elements/subtrees that are never meaningful "page content" to read
  // aloud: the assistant widget itself, nav/structural chrome, raw
  // markup/scripts, AND (per the two-speakers separation) every chat
  // bubble on the assistant page — this floating Read Screen button must
  // NEVER read AI chat replies; replaying the assistant's latest reply is
  // the dedicated in-page speaker's job only (see assistant.html's
  // readScreenBtn, which calls SanadVoice.replayLast() directly and never
  // touches this file at all).
  const READ_SCREEN_SKIP_SELECTOR = [
    "#globalAssistantFab", "#globalAssistantMic", "#globalAssistantRead",
    ".global-assistant-cluster", "script", "style", "svg", "noscript",
    ".bottom-nav", ".sheet-handle", ".toast", ".mic-dock", ".voice-status-row",
    ".bubble", ".chat-area",
    "[data-no-read]",
  ].join(", ");

  // Only these tags/classes are ever candidates — deliberately narrow so
  // generic wrapper <div>s (which would otherwise duplicate their
  // children's text) never get read themselves. Chat bubbles are
  // deliberately NOT in this list (see READ_SCREEN_SKIP_SELECTOR above).
  const READ_SCREEN_CANDIDATE_SELECTOR = [
    "h1", "h2", "h3", "h4", "p", "label",
    ".amount", ".label", ".meta", ".sub", ".sheet-greeting",
    "button:not([aria-hidden])", "a.btn", ".btn",
    "input", "select", "textarea",
    ".list-item", ".suggestion-item", ".read-screen-btn",
    ".switch-row > span:first-child", ".demo-hint", ".section-title",
  ].join(", ");

  function elementReadableText(el) {
    const tag = el.tagName.toLowerCase();
    if (tag === "input") {
      if (el.type === "checkbox" || el.type === "radio") return "";
      return (el.value || el.placeholder || "").trim();
    }
    if (tag === "textarea") {
      return (el.value || el.placeholder || "").trim();
    }
    if (tag === "select") {
      const selected = el.options[el.selectedIndex];
      return selected ? selected.textContent.trim() : "";
    }
    const direct = (el.textContent || "").trim();
    if (direct) return direct;
    // Icon-only controls (mic/send buttons etc) carry their label in
    // title/aria-label instead of visible text — fall back to those so
    // they're still announced instead of silently skipped.
    return (el.getAttribute("aria-label") || el.getAttribute("title") || "").trim();
  }

  // Filters out text that's purely decorative/technical noise: bare
  // punctuation, numeric-only IDs, or anything that still looks like an
  // icon-only glyph slipped through (defensive — icons are all SVG now
  // and already excluded above, but keeps this robust either way).
  function isMeaningfulText(text) {
    if (!text) return false;
    if (text.length > 600) return false; // too long to be real UI content — likely a wrapper
    if (/^[\s\u200e\u200f.,:؛،\-_/\\]*$/.test(text)) return false;
    return true;
  }

  function collectScreenText() {
    const root = document.querySelector(".app-shell") || document.body;
    const seen = new Set();
    const parts = [];

    const candidates = root.querySelectorAll(READ_SCREEN_CANDIDATE_SELECTOR);

    candidates.forEach((el) => {
      if (el.closest(READ_SCREEN_SKIP_SELECTOR)) return;
      if (!isVisible(el)) return;

      // Skip a candidate whose meaningful text is already fully covered by
      // an ancestor OR a descendant that's also in our candidate list —
      // keeps only the innermost/most specific readable node so nothing
      // gets spoken twice (e.g. a .list-item wrapping its own <span>s).
      if (el.closest(".list-item") && !el.classList.contains("list-item")) return;
      if (el.matches(".btn, button") && el.closest(".btn, button") !== el) return;

      const text = elementReadableText(el).replace(/\s+/g, " ").trim();
      if (!isMeaningfulText(text)) return;
      if (seen.has(text)) return;
      seen.add(text);
      parts.push(text);
    });

    if (!parts.length) {
      return `لا يوجد محتوى ظاهر لقراءته في ${currentPageLabel()} حالياً.`;
    }
    return `محتوى ${currentPageLabel()}: ` + parts.join("، ");
  }

  function speak(text, lang) {
    if (window.SanadVoice) {
      window.SanadVoice.speak(text, { lang: lang || "ar-SA" });
      return;
    }
    // Defensive fallback in the unlikely case voice_player.js failed to
    // load — keeps this widget working exactly as it did before the
    // voice subsystem existed.
    if (!("speechSynthesis" in window)) {
      showToast(text, "");
      return;
    }
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = lang || "ar-SA";
    window.speechSynthesis.speak(utter);
  }

  function readScreen() {
    const text = collectScreenText();
    showToast("جارِ قراءة الشاشة...", "");
    speak(text);
  }

  // ------------------------------------------------------------------
  // Action dispatch shared by voice + text input
  // ------------------------------------------------------------------
  async function sendToAssistant(message) {
    if (!message || !message.trim()) return;
    const { data, ok } = await apiFetch("/api/assistant", {
      method: "POST",
      body: { message, context: { page: window.SANAD_PAGE || "unknown" } },
    });

    if (!ok) {
      showToast("تعذر الاتصال بالمساعد، حاول مرة أخرى", "error");
      return;
    }

    const responseText = data.response || "عذراً، حدث خطأ.";
    showToast(responseText, "");
    speak(responseText);

    const action = data.action || {};
    if (action.readScreen) {
      setTimeout(readScreen, 300);
    } else if (action.goBack) {
      setTimeout(() => window.history.back(), 800);
    } else if (action.close) {
      // Nothing to close globally; acknowledged via the spoken/toast reply.
    } else if (action.navigate) {
      setTimeout(() => { window.location.href = action.navigate; }, 1200);
    }
  }

  // ------------------------------------------------------------------
  // Widget wiring
  // ------------------------------------------------------------------
  function init() {
    const micBtn = document.getElementById("globalAssistantMic");
    const readBtn = document.getElementById("globalAssistantRead");
    if (!micBtn || !readBtn) return;

    readBtn.addEventListener("click", readScreen);

    const micController = createMicController({
      lang: () => "ar-SA",
      onTranscript: (text) => sendToAssistant(text),
      onListeningChange: (isListening) => micBtn.classList.toggle("listening", isListening),
      onStatus: (message, type) => showToast(message, type),
    });

    micBtn.addEventListener("click", () => micController.toggle());
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Exposed so accessibility.js (Senior Mode's automatic screen reading)
  // can reuse this exact DOM-walking + speak logic instead of duplicating
  // it — the global widget's "read screen" button and Senior Mode's
  // auto-read both end up calling the same function.
  window.SanadGlobalAssistant = { readScreen, collectScreenText };
})();
