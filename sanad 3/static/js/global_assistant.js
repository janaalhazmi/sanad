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
  // Read Screen: dynamically walk the visible DOM and build a spoken
  // summary. Nothing here is hardcoded per-page.
  // ------------------------------------------------------------------
  function isVisible(el) {
    return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  }

  function collectScreenText() {
    const root = document.querySelector(".app-shell") || document.body;
    const skipSelectors = "#globalAssistantFab, #globalAssistantMic, #globalAssistantRead, script, style";
    const parts = [];
    const seen = new Set();

    const selector = [
      "h1", "h2", "h3", "p", "label", ".amount", ".label", ".meta",
      ".list-item", ".bubble", "button", ".btn", "a.btn", "input", "select",
      ".switch-row span:first-child", ".demo-hint",
    ].join(", ");

    root.querySelectorAll(selector).forEach((el) => {
      if (el.closest(skipSelectors)) return;
      if (!isVisible(el)) return;

      let text = "";
      const tag = el.tagName.toLowerCase();
      if (tag === "input") {
        const label = el.value || el.placeholder || "";
        if (!label) return;
        text = label;
      } else if (tag === "select") {
        const selected = el.options[el.selectedIndex];
        text = selected ? selected.textContent.trim() : "";
      } else {
        text = el.textContent.trim();
      }

      text = text.replace(/\s+/g, " ").trim();
      if (!text || text.length > 200) return;
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

    const SpeechRecognitionImpl = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognitionImpl) {
      micBtn.addEventListener("click", () => {
        const said = window.prompt("متصفحك لا يدعم التعرف على الصوت. اكتب طلبك هنا:");
        if (said) sendToAssistant(said);
      });
      return;
    }

    const recognition = new SpeechRecognitionImpl();
    recognition.lang = "ar-SA";
    recognition.continuous = false;
    recognition.interimResults = false;
    let listening = false;

    recognition.onresult = (event) => sendToAssistant(event.results[0][0].transcript);
    recognition.onerror = () => {
      listening = false;
      micBtn.classList.remove("listening");
      showToast("تعذر التعرف على الصوت، حاول مرة أخرى", "error");
    };
    recognition.onend = () => {
      listening = false;
      micBtn.classList.remove("listening");
    };

    micBtn.addEventListener("click", () => {
      if (listening) { recognition.stop(); return; }
      try {
        recognition.start();
        listening = true;
        micBtn.classList.add("listening");
      } catch (e) {
        showToast("تعذر تشغيل الميكروفون", "error");
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
