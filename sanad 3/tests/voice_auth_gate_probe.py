#!/usr/bin/python3
"""Tests the FULL security-gate integration against the live server: with
Voice Authentication enabled, a transfer must go through OTP/WebAuthn AND
THEN voice — never bypassing either, never letting voice stand alone."""
import base64
import json
import sys

import requests

sys.path.insert(0, "/home/claude/sanad")
import voice_auth.embeddings as emb_mod
import numpy as np

BASE = "http://127.0.0.1:5001"
s = requests.Session()
results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def fake_sample(speaker_id, seed):
    return f"{speaker_id}:{seed}".encode().ljust(200, b"\x00")


# NOTE: this test drives the LIVE server process, so we can't monkeypatch
# its in-process provider from here the way the pure-Python probes did.
# Instead we rely on the server having zero real providers installed
# (this sandbox's actual state) to test the "voice auth enabled but
# provider unavailable -> fail closed" path for real, end to end -- the
# single most important behavior to prove against the real running app.
# The enrollment/accept/reject MATH is already proven in
# tests/voice_auth_engine_probe.py with a fake provider in-process.

s.post(f"{BASE}/api/login", json={"phone": "0500000000", "password": "12345"})

# ---- Status before enrollment ----
r = s.get(f"{BASE}/api/voice-auth/status")
check("status endpoint works", r.ok and r.json()["success"])
status = r.json()["status"]
check("not enrolled initially", not status["enrolled"])
check("not available (no provider installed in this sandbox)", status["available"] is False)

# ---- Enrollment fails cleanly when no provider is installed ----
samples = [b64(fake_sample("jana", i)) for i in range(3)]
r = s.post(f"{BASE}/api/voice-auth/enroll", json={"samples": samples})
check(
    "enrollment fails cleanly (no crash) when no provider is installed",
    r.status_code == 400 and not r.json()["success"],
    r.json(),
)

# ---- Cannot enable without a real enrollment ----
r = s.post(f"{BASE}/api/voice-auth/settings", json={"enabled": True})
check("cannot enable voice auth without enrollment", r.status_code == 400 and not r.json()["success"])

# ---- Simulate an already-enrolled + enabled account by writing directly
#      to account.json (bypassing the (currently unavailable) real
#      enrollment path) — this is how we test the GATING logic for real
#      against the live server without needing a real provider installed.
account = json.load(open("/home/claude/sanad/account.json", encoding="utf-8"))
account["voice_auth"] = {
    "enabled": True, "enrolled": True, "provider": "speechbrain",
    "embeddings": [[0.1] * 192], "centroid": [0.1] * 192,
    "threshold": 0.75, "sample_count": 1, "enrolled_at": 1720000000,
}
json.dump(account, open("/home/claude/sanad/account.json", "w", encoding="utf-8"), ensure_ascii=False, indent=4)

# ---- Now attempt a transfer through OTP ----
r = s.post(f"{BASE}/api/action/create", json={"type": "transfer", "payload": {"beneficiary": "أحمد", "amount": 15}})
check("transfer action created", r.ok and r.json()["success"])

r = s.get(f"{BASE}/api/action/status")
check("status reports voice_auth_required=True now", r.json().get("voice_auth_required") is True, r.json())
check("status reports primary_verified=False before OTP", r.json().get("primary_verified") is False)

r = s.post(f"{BASE}/api/action/otp/send")
demo_code = r.json()["demo_code"]

before_balance = account["balance"]

r = s.post(f"{BASE}/api/action/otp/verify", json={"code": demo_code})
check(
    "OTP success with voice-auth enabled does NOT execute yet, asks for voice",
    r.ok and r.json().get("requires_voice") is True and r.json().get("success") is True,
    r.json(),
)

after_otp_balance = json.load(open("/home/claude/sanad/account.json", encoding="utf-8"))["balance"]
check("balance UNCHANGED after OTP alone (voice step still pending)", before_balance == after_otp_balance)

r = s.get(f"{BASE}/api/action/status")
check("status now reports primary_verified=True after OTP success", r.json().get("primary_verified") is True)

# ---- Attempting voice verify with the provider unavailable -> fails closed ----
fake_audio = b64(fake_sample("jana", 999))
r = s.post(f"{BASE}/api/action/voice/verify", json={"audio": fake_audio})
check(
    "voice verify FAILS CLOSED (provider unavailable) -> action still not executed",
    r.status_code == 400 and not r.json()["success"],
    r.json(),
)

final_balance = json.load(open("/home/claude/sanad/account.json", encoding="utf-8"))["balance"]
check("balance STILL unchanged after failed voice verify (fail-closed proven end-to-end)", before_balance == final_balance)

# ---- Pending action must still be there (not consumed by the failed attempt) ----
r = s.get(f"{BASE}/api/action/status")
check("pending action survives a failed voice verify attempt (user can retry or cancel)", r.ok and r.json()["success"])

# ---- Cannot call voice/verify without primary_verified (defense in depth) ----
s.post(f"{BASE}/api/action/cancel")
r = s.post(f"{BASE}/api/action/create", json={"type": "transfer", "payload": {"beneficiary": "أحمد", "amount": 5}})
r = s.post(f"{BASE}/api/action/voice/verify", json={"audio": fake_audio})
check(
    "voice verify REJECTED without prior OTP/WebAuthn success (cannot be used as a standalone bypass)",
    r.status_code == 400 and not r.json()["success"] and "التحقق الأساسي" in r.json().get("message", ""),
    r.json(),
)

s.post(f"{BASE}/api/action/cancel")

# ---- Cleanup: disable voice auth, restore clean account state ----
account2 = json.load(open("/home/claude/sanad/account.json", encoding="utf-8"))
account2.pop("voice_auth", None)
json.dump(account2, open("/home/claude/sanad/account.json", "w", encoding="utf-8"), ensure_ascii=False, indent=4)

print("\n=== SUMMARY ===")
print("ALL PASS" if all(results) else "SOME FAILED")
