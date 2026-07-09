// Shared voice-authentication recording helper for سَند.
//
// Captures a short microphone sample and produces base64-encoded 16kHz
// mono PCM16 audio — the exact byte format voice_auth/embeddings.py's
// providers expect (np.frombuffer(audio_bytes, dtype=np.int16)). Doing the
// resampling client-side avoids needing any server-side audio-decoding
// dependency (e.g. ffmpeg) for what MediaRecorder would otherwise produce
// (compressed webm/opus).
//
// Uses ScriptProcessorNode rather than AudioWorklet — technically
// deprecated, but universally supported with no separate module file to
// load, which matters more for a hackathon deployment than the deprecation
// warning.
(function () {
  function floatTo16BitPCM(float32Array) {
    const out = new Int16Array(float32Array.length);
    for (let i = 0; i < float32Array.length; i++) {
      const s = Math.max(-1, Math.min(1, float32Array[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out;
  }

  function resampleLinear(float32Array, fromRate, toRate) {
    if (fromRate === toRate) return float32Array;
    const ratio = fromRate / toRate;
    const newLength = Math.round(float32Array.length / ratio);
    const result = new Float32Array(newLength);
    for (let i = 0; i < newLength; i++) {
      const srcIndex = i * ratio;
      const i0 = Math.floor(srcIndex);
      const i1 = Math.min(i0 + 1, float32Array.length - 1);
      const frac = srcIndex - i0;
      result[i] = float32Array[i0] * (1 - frac) + float32Array[i1] * frac;
    }
    return result;
  }

  function int16ToBase64(int16Array) {
    const bytes = new Uint8Array(int16Array.buffer);
    let binary = "";
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    return btoa(binary);
  }

  /**
   * Records for `durationMs` milliseconds and resolves with a base64
   * string of 16kHz mono PCM16 audio. Rejects if microphone access is
   * denied or unsupported.
   */
  async function recordSample(durationMs) {
    durationMs = durationMs || 3000;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error("المتصفح لا يدعم تسجيل الصوت");
    }

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const AudioContextImpl = window.AudioContext || window.webkitAudioContext;
    const audioCtx = new AudioContextImpl();
    const source = audioCtx.createMediaStreamSource(stream);
    const processor = audioCtx.createScriptProcessor(4096, 1, 1);
    const chunks = [];

    source.connect(processor);
    processor.connect(audioCtx.destination);

    processor.onaudioprocess = (event) => {
      const channelData = event.inputBuffer.getChannelData(0);
      chunks.push(new Float32Array(channelData));
    };

    await new Promise((resolve) => setTimeout(resolve, durationMs));

    processor.disconnect();
    source.disconnect();
    stream.getTracks().forEach((t) => t.stop());
    const nativeSampleRate = audioCtx.sampleRate;
    await audioCtx.close();

    const totalLength = chunks.reduce((sum, c) => sum + c.length, 0);
    const merged = new Float32Array(totalLength);
    let offset = 0;
    for (const c of chunks) {
      merged.set(c, offset);
      offset += c.length;
    }

    const resampled = resampleLinear(merged, nativeSampleRate, 16000);
    const pcm16 = floatTo16BitPCM(resampled);
    return int16ToBase64(pcm16);
  }

  window.SanadVoiceAuthRecorder = { recordSample };
})();
