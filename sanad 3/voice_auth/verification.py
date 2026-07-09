#!/usr/bin/python3
"""
Verification logic for Voice Authentication: given a fresh audio sample
and a stored profile (provider name + centroid), decide accept/reject with
a confidence score. This module is intentionally "dumb" (no storage, no
session/account access) so its correctness is easy to verify in isolation
with plain vectors.
"""

import numpy as np

from . import embeddings as emb_mod


class VerificationError(Exception):
    """Raised when verification cannot be performed at all (provider
    unavailable, corrupt profile, bad audio) — distinct from a normal
    "voice didn't match" rejection, which is a successful, valid decision,
    just a negative one. Callers (engine.py) MUST treat this exception as
    fail-closed: block the action, don't silently pass."""


def _cosine(a, b) -> float:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    denom = (np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def verify(audio_sample: bytes, profile: dict) -> dict:
    """Returns {"accepted": bool, "confidence": float, "threshold": float}.
    Raises VerificationError if the check could not be performed at all
    (this is different from a low-confidence rejection, which is returned
    normally with accepted=False)."""
    provider_name = profile.get("provider")
    centroid = profile.get("centroid")
    threshold = float(profile.get("threshold", 0.75))

    if not provider_name or not centroid:
        raise VerificationError("لا يوجد ملف صوتي مسجل لهذا الحساب")

    provider = emb_mod.get_provider(provider_name)
    if provider is None or not provider.is_available():
        raise VerificationError("خدمة التحقق الصوتي غير متاحة حالياً")

    if not audio_sample or len(audio_sample) < 100:
        raise VerificationError("العينة الصوتية فارغة أو قصيرة جداً")

    try:
        vector = provider.embed(audio_sample)
    except emb_mod.VoiceAuthUnavailable as e:
        raise VerificationError(f"تعذر معالجة الصوت: {e}")

    confidence = _cosine(vector, centroid)
    # Cosine similarity is in [-1, 1]; clamp negatives to 0 for a cleaner
    # "confidence" number to show the user (a negative score has no
    # intuitive meaning as a percentage-like confidence).
    confidence = max(0.0, confidence)

    return {
        "accepted": confidence >= threshold,
        "confidence": confidence,
        "threshold": threshold,
    }
