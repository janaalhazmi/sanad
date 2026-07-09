#!/usr/bin/python3
"""
Speaker-embedding provider abstraction for Voice Authentication.

Mirrors voice/providers.py's pattern (TTSProvider -> EdgeTTSProvider/
OpenAITTSProvider), but with one critical difference in how callers must
treat unavailability: TTS is a convenience feature (silent fallback to the
browser voice is fine). Voice AUTHENTICATION is a security factor — if the
user has enabled it, "the model isn't installed" must result in the
transaction being BLOCKED by the caller (voice_auth/engine.py), never
silently skipped. This module only reports availability/does the math; it
does not decide what to do about unavailability — that policy lives in
engine.py, kept deliberately separate so the fail-closed decision is easy
to audit in one place.

Every provider returns a fixed-length numpy embedding vector for a given
audio sample. Embeddings from different providers are NOT comparable to
each other, so the enrolled profile always records which provider produced
it, and verification always uses that same provider.
"""

import os


class VoiceAuthUnavailable(Exception):
    """Raised when embedding extraction cannot be performed right now
    (missing package, model load failure, corrupt/empty audio)."""


class SpeakerEmbeddingProvider:
    name = "base"
    embedding_dim = None

    def is_available(self) -> bool:
        raise NotImplementedError

    def embed(self, audio_bytes: bytes, sample_rate: int = 16000):
        """Returns a 1-D numpy array embedding for the given raw audio
        (PCM16 mono bytes at sample_rate, or a container format the
        provider knows how to decode). Raises VoiceAuthUnavailable on any
        failure — never returns a fabricated/zero vector."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# SpeechBrain ECAPA-TDNN — primary provider (see design writeup: best
# accuracy/deployment-ease balance for this use case). Model:
# speechbrain/spkrec-ecapa-voxceleb, ~20MB, downloaded once from
# HuggingFace Hub on first use and cached locally thereafter (fully
# offline after that point).
# ---------------------------------------------------------------------------
class SpeechBrainProvider(SpeakerEmbeddingProvider):
    name = "speechbrain"
    embedding_dim = 192  # ECAPA-TDNN's output size for this checkpoint

    _model = None

    def is_available(self) -> bool:
        try:
            import speechbrain  # noqa: F401
            import torch  # noqa: F401
            return True
        except ImportError:
            return False

    def _get_model(self):
        if SpeechBrainProvider._model is not None:
            return SpeechBrainProvider._model
        try:
            from speechbrain.inference.speaker import EncoderClassifier
        except ImportError as e:
            raise VoiceAuthUnavailable(f"speechbrain not installed: {e}")
        try:
            SpeechBrainProvider._model = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir=os.path.join(os.path.expanduser("~"), ".cache", "sanad_speechbrain"),
            )
        except Exception as e:
            raise VoiceAuthUnavailable(f"failed to load speechbrain model: {e}")
        return SpeechBrainProvider._model

    def embed(self, audio_bytes: bytes, sample_rate: int = 16000):
        import numpy as np
        try:
            import torch
        except ImportError as e:
            raise VoiceAuthUnavailable(f"torch not installed: {e}")

        model = self._get_model()
        try:
            pcm = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            if pcm.size == 0:
                raise VoiceAuthUnavailable("empty audio sample")
            waveform = torch.tensor(pcm).unsqueeze(0)
            with torch.no_grad():
                embedding = model.encode_batch(waveform)
            vector = embedding.squeeze().cpu().numpy()
            return vector
        except VoiceAuthUnavailable:
            raise
        except Exception as e:
            raise VoiceAuthUnavailable(f"speechbrain embedding failed: {e}")


# ---------------------------------------------------------------------------
# Resemblyzer — lightweight fallback (GE2E d-vector, ~1.5MB model, minimal
# dependencies). Weaker accuracy than ECAPA-TDNN but a near-zero-friction
# install if torch/speechbrain can't be set up in time.
# ---------------------------------------------------------------------------
class ResemblyzerProvider(SpeakerEmbeddingProvider):
    name = "resemblyzer"
    embedding_dim = 256

    _encoder = None

    def is_available(self) -> bool:
        try:
            import resemblyzer  # noqa: F401
            return True
        except ImportError:
            return False

    def _get_encoder(self):
        if ResemblyzerProvider._encoder is not None:
            return ResemblyzerProvider._encoder
        try:
            from resemblyzer import VoiceEncoder
        except ImportError as e:
            raise VoiceAuthUnavailable(f"resemblyzer not installed: {e}")
        try:
            ResemblyzerProvider._encoder = VoiceEncoder()
        except Exception as e:
            raise VoiceAuthUnavailable(f"failed to load resemblyzer encoder: {e}")
        return ResemblyzerProvider._encoder

    def embed(self, audio_bytes: bytes, sample_rate: int = 16000):
        import numpy as np
        encoder = self._get_encoder()
        try:
            pcm = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            if pcm.size == 0:
                raise VoiceAuthUnavailable("empty audio sample")
            return encoder.embed_utterance(pcm)
        except VoiceAuthUnavailable:
            raise
        except Exception as e:
            raise VoiceAuthUnavailable(f"resemblyzer embedding failed: {e}")


# ---------------------------------------------------------------------------
# Dependency-free spectral fallback — guarantees Voice Authentication is
# always usable even when neither speechbrain+torch nor resemblyzer are
# installed (both are large/optional deps that may not be present in every
# deployment). Uses only numpy (already a hard, required dependency of this
# whole app) to build a fixed-length log-spaced spectral-energy fingerprint
# per audio sample — a lightweight, MFCC-style stand-in for a real neural
# speaker embedding. Materially less accurate than the two providers above,
# but never silently unavailable, so the security-sensitive fail-closed
# behavior in engine.py always has something real to fail closed WITH
# rather than being permanently blocked for lack of an optional package.
# ---------------------------------------------------------------------------
class NumpySpectralProvider(SpeakerEmbeddingProvider):
    name = "numpy_spectral"
    embedding_dim = 40

    def is_available(self) -> bool:
        try:
            import numpy  # noqa: F401
            return True
        except ImportError:
            return False

    def embed(self, audio_bytes: bytes, sample_rate: int = 16000):
        import numpy as np

        pcm = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float64) / 32768.0
        if pcm.size < sample_rate // 10:  # need at least ~100ms of audio
            raise VoiceAuthUnavailable("عينة الصوت قصيرة جداً لاستخراج بصمة صوتية")

        frame_len = max(int(sample_rate * 0.025), 2)
        hop_len = max(int(sample_rate * 0.010), 1)
        n_bins = self.embedding_dim

        freqs = np.fft.rfftfreq(frame_len, d=1.0 / sample_rate)
        nyquist = freqs[-1] if freqs[-1] > 50 else 51.0
        edges = np.geomspace(50, nyquist, n_bins + 1)
        window = np.hanning(frame_len)

        accum = np.zeros(n_bins, dtype=np.float64)
        n_frames = 0
        for start in range(0, max(len(pcm) - frame_len, 0) + 1, hop_len):
            frame = pcm[start:start + frame_len]
            if len(frame) < frame_len:
                break
            spectrum = np.abs(np.fft.rfft(frame * window))
            for i in range(n_bins):
                mask = (freqs >= edges[i]) & (freqs < edges[i + 1])
                if mask.any():
                    accum[i] += spectrum[mask].mean()
            n_frames += 1

        if n_frames == 0:
            raise VoiceAuthUnavailable("عينة الصوت قصيرة جداً لاستخراج بصمة صوتية")

        vector = np.log1p(accum / n_frames)
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector = vector / norm
        return vector.astype(np.float32)


PROVIDER_REGISTRY = {
    "speechbrain": SpeechBrainProvider,
    "resemblyzer": ResemblyzerProvider,
    "numpy_spectral": NumpySpectralProvider,
}

_provider_instances = {
    "speechbrain": SpeechBrainProvider(),
    "resemblyzer": ResemblyzerProvider(),
    "numpy_spectral": NumpySpectralProvider(),
}


def get_provider(name: str):
    return _provider_instances.get(name)


def first_available_provider():
    """Preferred order: speechbrain (best accuracy), then resemblyzer
    (lightweight fallback), then the dependency-free numpy_spectral
    provider — which is always available as long as numpy is installed
    (a hard requirement of this app already), so Voice Authentication can
    never be permanently stuck "unavailable" for lack of an optional
    heavy package."""
    for name in ("speechbrain", "resemblyzer", "numpy_spectral"):
        provider = _provider_instances[name]
        if provider.is_available():
            return provider
    return None
