// Server-side speech-to-text fallback (Whisper via /api/stt), used when
// the browser has no native SpeechRecognition support at all, or when it
// fails to actually start/work (see createMicController() in main.js,
// which decides WHEN to use this). Records real microphone audio via
// MediaRecorder and uploads it once stopped — this is a genuine capture,
// not a simulation.
(function () {
  async function recordAndTranscribe(lang) {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error("الوصول إلى الميكروفون غير مدعوم في هذا المتصفح");
    }
    if (typeof MediaRecorder === "undefined") {
      throw new Error("تسجيل الصوت غير مدعوم في هذا المتصفح");
    }

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

    const mimeType =
      MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" :
      MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" :
      MediaRecorder.isTypeSupported("audio/mp4") ? "audio/mp4" : "";

    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    const chunks = [];
    recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };

    const stopped = new Promise((resolve) => { recorder.onstop = resolve; });
    recorder.start();
    console.log("SanadSTTFallback: recording started, mimeType=", mimeType || "(browser default)");

    return {
      stopAndTranscribe: async () => {
        recorder.stop();
        await stopped;
        stream.getTracks().forEach((t) => t.stop());

        const blob = new Blob(chunks, { type: mimeType || "audio/webm" });
        console.log(`SanadSTTFallback: recording stopped, ${blob.size} bytes, uploading for transcription...`);
        if (!blob.size) {
          throw new Error("لم يتم تسجيل أي صوت");
        }

        const form = new FormData();
        form.append("audio", blob, "speech.webm");
        form.append("lang", (lang || "ar-SA").slice(0, 2));

        const res = await fetch("/api/stt", { method: "POST", credentials: "same-origin", body: form });
        let data = {};
        try { data = await res.json(); } catch (e) { data = {}; }
        if (!res.ok || !data.success) {
          throw new Error(data.message || "تعذر التعرف على الصوت عبر الخادم");
        }
        return data.text || "";
      },
      cancel: () => {
        try { recorder.stop(); } catch (e) {}
        stream.getTracks().forEach((t) => t.stop());
      },
    };
  }

  window.SanadSTTFallback = { recordAndTranscribe };
})();
