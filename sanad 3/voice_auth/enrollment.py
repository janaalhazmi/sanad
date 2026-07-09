#!/usr/bin/python3
"""
Enrollment orchestration for Voice Authentication.

Takes N raw audio samples (already captured client-side as short spoken
phrases), extracts one embedding per sample via the resolved provider, and
computes a centroid (mean vector) to store as the profile. Rejects the
whole enrollment attempt if the samples are inconsistent with each other
(a same-speaker sanity check) rather than silently averaging in a bad
sample — e.g. background noise, a misread phrase, or someone else's voice
getting mixed into the enrollment set.

Raw audio is only ever held in local variables here for the duration of
embedding extraction, then goes out of scope — nothing in this module
writes audio to disk or persists it anywhere.
"""

import numpy as np

from . import embeddings as emb_mod

MIN_SAMPLES = 3
MAX_SAMPLES = 5
# Minimum pairwise cosine similarity samples must have with each other to
# be accepted as "clearly the same speaker" — deliberately looser than the
# verification threshold (0.75 default), since enrollment samples are
# compared to EACH OTHER (noisier signal) rather than to a clean centroid.
MIN_INTRA_SAMPLE_CONSISTENCY = 0.5


class EnrollmentError(Exception):
    """User-facing enrollment failure (bad audio, inconsistent samples,
    provider unavailable) — always carries an Arabic message."""


def _cosine(a, b) -> float:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    denom = (np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def enroll(audio_samples: list, provider=None) -> dict:
    """audio_samples: list of raw PCM16 mono audio bytes (3-5 short
    recordings). Returns {"provider": str, "embeddings": [[float,...],...],
    "centroid": [float,...]}. Raises EnrollmentError on any failure —
    never returns a partial/best-effort profile."""
    if not (MIN_SAMPLES <= len(audio_samples) <= MAX_SAMPLES):
        raise EnrollmentError(f"يجب تسجيل بين {MIN_SAMPLES} و {MAX_SAMPLES} عينات صوتية")

    provider = provider or emb_mod.first_available_provider()
    if provider is None:
        raise EnrollmentError("خدمة التحقق الصوتي غير متاحة حالياً، حاول لاحقاً")

    vectors = []
    for i, sample in enumerate(audio_samples):
        if not sample or len(sample) < 100:
            raise EnrollmentError(f"العينة رقم {i + 1} فارغة أو قصيرة جداً، أعد التسجيل")
        try:
            vector = provider.embed(sample)
        except emb_mod.VoiceAuthUnavailable as e:
            raise EnrollmentError(f"تعذر معالجة العينة رقم {i + 1}: {e}")
        vectors.append(np.asarray(vector, dtype=np.float64))

    # Consistency check: every sample must resemble every other sample
    # reasonably well. This catches "recorded the wrong phrase", "someone
    # else spoke one sample", background-noise-dominated clips, etc.
    n = len(vectors)
    min_pairwise = 1.0
    for i in range(n):
        for j in range(i + 1, n):
            sim = _cosine(vectors[i], vectors[j])
            min_pairwise = min(min_pairwise, sim)

    if min_pairwise < MIN_INTRA_SAMPLE_CONSISTENCY:
        raise EnrollmentError(
            "العينات الصوتية غير متطابقة بما يكفي، تأكد من التسجيل بنفس الصوت "
            "وفي بيئة هادئة، ثم أعد المحاولة"
        )

    centroid = np.mean(vectors, axis=0)
    # Re-normalize the centroid so verification's cosine similarity
    # behaves consistently regardless of how many samples went in.
    norm = np.linalg.norm(centroid)
    if norm > 0:
        centroid = centroid / norm

    return {
        "provider": provider.name,
        "embeddings": [v.tolist() for v in vectors],
        "centroid": centroid.tolist(),
    }
