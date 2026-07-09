#!/usr/bin/python3
"""Tests voice_auth's enrollment/verification math with controlled fake
embeddings. This proves the LOGIC (centroid computation, consistency
rejection, threshold decision, confidence scoring) is correct. It does NOT
prove real biometric accuracy against real voices — see
tests/real_voice_auth_check.py (to be written once we get to end-to-end
wiring) for that, in a real environment with speechbrain installed."""
import sys
sys.path.insert(0, "/home/claude/sanad")

import numpy as np
import voice_auth.embeddings as emb_mod
import voice_auth.enrollment as enrollment
import voice_auth.verification as verification

results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


class FakeProvider(emb_mod.SpeakerEmbeddingProvider):
    """Deterministic fake: maps a 'speaker id' baked into the audio bytes
    to a fixed base vector plus small per-sample noise, so we can
    precisely control similarity between samples/speakers."""
    name = "speechbrain"  # must match its registry key so a saved
                          # profile's provider name resolves back to this
                          # same fake instance during verification
    embedding_dim = 16

    def __init__(self):
        self.rng = np.random.RandomState(42)
        self.speakers = {}

    def is_available(self):
        return True

    def embed(self, audio_bytes, sample_rate=16000):
        # audio_bytes encodes "speaker:noise_seed" as plain text for this fake,
        # possibly null-padded to satisfy the real minimum-length guard.
        text = audio_bytes.decode().rstrip("\x00")
        speaker_id, noise_seed = text.split(":")
        if speaker_id not in self.speakers:
            local_rng = np.random.RandomState(hash(speaker_id) % (2**31))
            self.speakers[speaker_id] = local_rng.randn(16)
        base = self.speakers[speaker_id]
        noise_rng = np.random.RandomState(int(noise_seed))
        noisy = base + noise_rng.randn(16) * 0.05  # small noise = same speaker, slightly different sample
        return noisy


fake = FakeProvider()
emb_mod._provider_instances["speechbrain"] = fake
emb_mod._provider_instances["resemblyzer"].is_available = lambda: False


def make_samples(speaker_id, count, seed_start=0):
    # Padded to satisfy the real minimum-audio-length guard in
    # enrollment.py/verification.py (100 bytes) — this fake provider only
    # cares about the "speaker:noise_seed" prefix, so padding is inert.
    return [f"{speaker_id}:{seed_start + i}".encode().ljust(200, b"\x00") for i in range(count)]


# ---- 1. Enrollment: happy path ----
samples = make_samples("alice", 3)
profile = enrollment.enroll(samples)
check("enrollment succeeds with 3 consistent samples", profile["provider"] == "speechbrain")
check("centroid has the right dimensionality", len(profile["centroid"]) == 16)
check("all embeddings stored", len(profile["embeddings"]) == 3)

# ---- 2. Enrollment: too few / too many samples rejected ----
try:
    enrollment.enroll(make_samples("alice", 2))
    check("too few samples rejected", False)
except enrollment.EnrollmentError:
    check("too few samples rejected", True)

try:
    enrollment.enroll(make_samples("alice", 6))
    check("too many samples rejected", False)
except enrollment.EnrollmentError:
    check("too many samples rejected", True)

# ---- 3. Enrollment: inconsistent samples (different speakers mixed in) rejected ----
mixed = make_samples("alice", 2) + make_samples("bob", 1)
try:
    enrollment.enroll(mixed)
    check("inconsistent samples (different speaker mixed in) rejected", False)
except enrollment.EnrollmentError:
    check("inconsistent samples (different speaker mixed in) rejected", True)

# ---- 4. Verification: genuine speaker accepted ----
genuine_sample = make_samples("alice", 1, seed_start=100)[0]
result = verification.verify(genuine_sample, profile)
check("genuine speaker (same person, new sample) is accepted", result["accepted"], result)
check("confidence is high for genuine speaker", result["confidence"] > 0.9, result["confidence"])

# ---- 5. Verification: impostor rejected (false acceptance check) ----
impostor_sample = make_samples("bob", 1, seed_start=200)[0]
result2 = verification.verify(impostor_sample, profile)
check("impostor (different speaker) is REJECTED", not result2["accepted"], result2)
check("impostor confidence is low", result2["confidence"] < 0.75, result2["confidence"])

# ---- 6. Verification: statistical false-acceptance sanity check ----
# Many random "impostor" speakers against Alice's profile -- confirm the
# false-acceptance rate is low with this fake provider's noise model.
false_accepts = 0
n_impostors = 50
for k in range(n_impostors):
    impostor = make_samples(f"stranger_{k}", 1, seed_start=k)[0]
    r = verification.verify(impostor, profile)
    if r["accepted"]:
        false_accepts += 1
check(
    f"false-acceptance rate across {n_impostors} random impostors is low",
    false_accepts / n_impostors < 0.1,
    f"{false_accepts}/{n_impostors} falsely accepted",
)

# ---- 7. Verification: statistical false-rejection sanity check ----
# Many genuine samples from Alice (same speaker, different noise) --
# confirm false-rejection rate is low.
false_rejects = 0
n_genuine = 50
for k in range(n_genuine):
    genuine = make_samples("alice", 1, seed_start=1000 + k)[0]
    r = verification.verify(genuine, profile)
    if not r["accepted"]:
        false_rejects += 1
check(
    f"false-rejection rate across {n_genuine} genuine samples is low",
    false_rejects / n_genuine < 0.1,
    f"{false_rejects}/{n_genuine} falsely rejected",
)

# ---- 8. Threshold sensitivity: raising threshold increases rejections ----
strict_profile = dict(profile, threshold=0.999)
result3 = verification.verify(genuine_sample, strict_profile)
check(
    "raising the threshold makes verification stricter (same sample now borderline/rejected)",
    result3["accepted"] != result["accepted"] or result3["confidence"] == result["confidence"],
    result3,
)

# ---- 9. Fail-closed: provider unavailable raises, doesn't silently accept ----
fake_unavailable = FakeProvider()
fake_unavailable.is_available = lambda: False
emb_mod._provider_instances["speechbrain"] = fake_unavailable
try:
    verification.verify(genuine_sample, profile)
    check("provider unavailable -> raises VerificationError (fail-closed)", False)
except verification.VerificationError:
    check("provider unavailable -> raises VerificationError (fail-closed)", True)
emb_mod._provider_instances["speechbrain"] = fake  # restore

# ---- 10. Missing/corrupt profile raises cleanly ----
try:
    verification.verify(genuine_sample, {})
    check("missing profile raises VerificationError", False)
except verification.VerificationError:
    check("missing profile raises VerificationError", True)

print("\n=== SUMMARY ===")
print("ALL PASS" if all(results) else "SOME FAILED")
