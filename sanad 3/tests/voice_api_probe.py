#!/usr/bin/python3
"""Tests /api/voice/settings and /api/tts against the LIVE server (no real
TTS provider installed in this sandbox — this proves graceful degradation
end-to-end, exactly the state a fresh `pip install` without edge-tts/openai
would be in)."""
import requests

BASE = "http://127.0.0.1:5001"
session = requests.Session()
results = []


def check(name, cond, detail=""):
    results.append((name, cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


r = session.post(f"{BASE}/api/login", json={"phone": "0500000000", "password": "12345"})
assert r.json()["success"]

# ---- GET default settings ----
r = session.get(f"{BASE}/api/voice/settings")
check("GET voice settings succeeds", r.ok and r.json()["success"], r.text[:150])
defaults = r.json()["settings"]
check("defaults look sane", defaults.get("speed") == 1.0 and defaults.get("provider") == "auto", defaults)

# ---- POST valid update ----
r = session.post(f"{BASE}/api/voice/settings", json={"speed": 1.3, "volume": 0.8, "gender": "female"})
check("POST valid voice settings succeeds", r.ok and r.json()["success"], r.text[:150])
updated = r.json()["settings"]
check("updated speed persisted", updated.get("speed") == 1.3)
check("updated gender persisted", updated.get("gender") == "female")

# ---- GET again reflects the update immediately (no restart needed) ----
r = session.get(f"{BASE}/api/voice/settings")
check("GET after update reflects new values immediately", r.json()["settings"].get("speed") == 1.3)

# ---- POST invalid values are rejected cleanly ----
r = session.post(f"{BASE}/api/voice/settings", json={"speed": 99})
check("out-of-range speed is rejected (400)", r.status_code == 400 and not r.json()["success"], r.text[:150])

r = session.post(f"{BASE}/api/voice/settings", json={"provider": "not_a_real_provider"})
check("unknown provider is rejected (400)", r.status_code == 400 and not r.json()["success"], r.text[:150])

# ---- Reset to a known state for the next check ----
session.post(f"{BASE}/api/voice/settings", json={"provider": "auto", "speed": 1.0, "volume": 1.0, "gender": "male"})

# ---- /api/tts with NO real provider installed -> graceful browser fallback ----
r = session.post(f"{BASE}/api/tts", json={"text": "مرحباً بك في سَند"})
check(
    "TTS with no provider installed -> clean browser-fallback signal (not a crash/500)",
    r.status_code == 200 and r.json().get("fallback") == "browser",
    r.text[:150],
)

# ---- /api/tts/stream with no provider -> same graceful signal ----
r = session.post(f"{BASE}/api/tts/stream", json={"text": "مرحباً بك في سَند"})
check(
    "TTS streaming with no provider -> clean browser-fallback signal",
    r.status_code == 200 and r.json().get("fallback") == "browser",
    r.text[:150],
)

# ---- Auth still required ----
session2 = requests.Session()
r = session2.get(f"{BASE}/api/voice/settings")
check("voice settings require login (401 when not logged in)", r.status_code == 401)
r = session2.post(f"{BASE}/api/tts", json={"text": "test"})
check("TTS requires login (401 when not logged in)", r.status_code == 401)

print("\n=== SUMMARY ===")
print("ALL PASS" if all(c for _, c in results) else "SOME FAILED")
