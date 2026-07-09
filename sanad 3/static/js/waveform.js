// Real animated microphone waveform for سَند — draws bars driven by live
// Web Audio AnalyserNode frequency data (not a fixed CSS keyframe loop),
// so it genuinely reflects what the microphone is picking up.
(function () {
  function create(containerEl, barCount) {
    barCount = barCount || 20;
    containerEl.innerHTML = "";
    containerEl.classList.add("waveform");
    const bars = [];
    for (let i = 0; i < barCount; i++) {
      const bar = document.createElement("span");
      bar.className = "wf-bar";
      containerEl.appendChild(bar);
      bars.push(bar);
    }

    let audioCtx = null, analyser = null, source = null, rafId = null, dataArray = null;

    function tick() {
      if (!analyser) return;
      analyser.getByteFrequencyData(dataArray);
      const step = Math.max(1, Math.floor(dataArray.length / bars.length));
      for (let i = 0; i < bars.length; i++) {
        const v = dataArray[i * step] || 0;
        const scale = Math.max(0.08, v / 255);
        bars[i].style.transform = `scaleY(${scale.toFixed(2)})`;
      }
      rafId = requestAnimationFrame(tick);
    }

    function start(mediaStream) {
      stop();
      if (!mediaStream) return;
      try {
        const AudioContextImpl = window.AudioContext || window.webkitAudioContext;
        audioCtx = new AudioContextImpl();
        source = audioCtx.createMediaStreamSource(mediaStream);
        analyser = audioCtx.createAnalyser();
        analyser.fftSize = 64;
        analyser.smoothingTimeConstant = 0.65;
        dataArray = new Uint8Array(analyser.frequencyBinCount);
        source.connect(analyser);
        containerEl.classList.add("active");
        tick();
      } catch (e) {
        // Purely cosmetic — never let a visualization failure affect
        // the actual recording/verification flow using this stream.
        stop();
      }
    }

    function stop() {
      if (rafId) cancelAnimationFrame(rafId);
      rafId = null;
      if (source) { try { source.disconnect(); } catch (e) {} }
      if (analyser) { try { analyser.disconnect(); } catch (e) {} }
      if (audioCtx) { try { audioCtx.close(); } catch (e) {} }
      audioCtx = null; analyser = null; source = null; dataArray = null;
      containerEl.classList.remove("active");
      bars.forEach((b) => { b.style.transform = "scaleY(0.08)"; });
    }

    return { start, stop };
  }

  window.SanadWaveform = { create };
})();
