#!/usr/bin/python3
"""Tests voice_auth/engine.py, with special focus on the fail-closed
policy: whenever the provider is unavailable while voice auth is enabled,
verify_for_pending_action() must return accepted=False — NEVER True, and
NEVER let an exception escape uncaught in a way that a caller might
misinterpret as "skip this check"."""
import sys
sys.path.insert(0, "/home/claude/sanad")

import numpy as np
import voice_auth.embeddings as emb_mod
import voice_auth.engine as engine

results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


class FakeProvider(emb_mod.SpeakerEmbeddingProvider):
    name = "speechbrain"
    embedding_dim = 16

    def __init__(self):
        self._available = True
        self.speakers = {}

    def is_available(self):
        return self._available

    def embed(self, audio_bytes, sample_rate=16000):
        text = audio_bytes.decode().rstrip("\x00")
        speaker_id, noise_seed = text.split(":")
        if speaker_id not in self.speakers:
            local_rng = np.random.RandomState(hash(speaker_id) % (2**31))
            self.speakers[speaker_id] = local_rng.randn(16)
        base = self.speakers[speaker_id]
        noise_rng = np.random.RandomState(int(noise_seed))
        return base + noise_rng.randn(16) * 0.05


def sample(speaker_id, seed):
    return f"{speaker_id}:{seed}".encode().ljust(200, b"\x00")


fake = FakeProvider()
emb_mod._provider_instances["speechbrain"] = fake
emb_mod._provider_instances["resemblyzer"].is_available = lambda: False

# ---- Status before enrollment ----
account = {"name": "Jana"}
status = engine.get_status(account)
check("status reports not enrolled/not enabled initially", not status["enrolled"] and not status["enabled"])
check("status correctly reports provider availability", status["available"] is True)

# ---- Enroll ----
samples = [sample("jana", i) for i in range(3)]
ok, err = engine.enroll(account, samples)
check("enrollment via engine succeeds", ok, err)
check("account now enrolled", engine.is_enrolled(account))
check("not enabled until explicit toggle", not engine.is_enabled(account))

# ---- Enable ----
ok, err = engine.update_settings(account, {"enabled": True})
check("enabling via engine succeeds", ok, err)
check("account now enabled", engine.is_enabled(account))

# ---- Genuine verification succeeds ----
result = engine.verify_for_pending_action(account, sample("jana", 999))
check("genuine speaker accepted via engine", result["accepted"] is True, result)
check("confidence returned to caller", isinstance(result["confidence"], float) and result["confidence"] > 0.9)

# ---- Impostor rejected ----
result2 = engine.verify_for_pending_action(account, sample("stranger", 1))
check("impostor rejected via engine", result2["accepted"] is False, result2)
check("rejection still returns a confidence score, not None", isinstance(result2["confidence"], float))

# ================================================================
# THE CRITICAL TEST: provider becomes unavailable mid-session
# (crashed, package uninstalled, model failed to load) while voice
# auth is enabled. Must fail CLOSED, not silently pass.
# ================================================================
fake._available = False
result3 = engine.verify_for_pending_action(account, sample("jana", 999))
check(
    "PROVIDER UNAVAILABLE -> accepted is False (FAIL-CLOSED, not skipped/passed)",
    result3["accepted"] is False,
    result3,
)
check("unavailable case gives a message (not a silent bare rejection)", bool(result3.get("message")))
check("unavailable case does not fabricate a confidence score", result3["confidence"] is None)
fake._available = True  # restore

# ---- Corrupted/missing profile also fails closed ----
broken_account = {"voice_auth": {"enabled": True, "enrolled": True, "provider": "speechbrain", "centroid": None}}
result4 = engine.verify_for_pending_action(broken_account, sample("jana", 1))
check("corrupted profile (no centroid) fails closed", result4["accepted"] is False, result4)

# ---- Calling verify when not enabled at all also fails closed (defensive) ----
disabled_account = {"name": "X"}
result5 = engine.verify_for_pending_action(disabled_account, sample("jana", 1))
check("verify on a non-enabled account fails closed (defensive)", result5["accepted"] is False, result5)

# ---- Delete profile ----
engine.delete(account)
check("delete removes enrollment", not engine.is_enrolled(account))
check("delete removes enabled state too", not engine.is_enabled(account))

print("\n=== SUMMARY ===")
print("ALL PASS" if all(results) else "SOME FAILED")
