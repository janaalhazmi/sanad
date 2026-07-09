#!/usr/bin/python3
"""Section 2 of the acceptance test: voice settings immediate apply +
persistence, exercised through the real server (a page refresh is
simulated by a fresh GET; logout/login is the real endpoint sequence)."""
import requests

BASE = "http://127.0.0.1:5001"
s = requests.Session()
results = []


def check(name, cond, detail=""):
    results.append((name, cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


s.post(f"{BASE}/api/login", json={"phone": "0500000000", "password": "12345"})

# ---- Provider changes immediately ----
r = s.post(f"{BASE}/api/voice/settings", json={"provider": "edge"})
check("Provider change accepted", r.ok and r.json()["success"])
r = s.get(f"{BASE}/api/voice/settings")
check("Provider change reflected immediately (no page reload needed)", r.json()["settings"]["provider"] == "edge")

# ---- Speed changes immediately ----
r = s.post(f"{BASE}/api/voice/settings", json={"speed": 1.4})
r = s.get(f"{BASE}/api/voice/settings")
check("Speed change reflected immediately", r.json()["settings"]["speed"] == 1.4)

# ---- Volume changes immediately ----
r = s.post(f"{BASE}/api/voice/settings", json={"volume": 0.6})
r = s.get(f"{BASE}/api/voice/settings")
check("Volume change reflected immediately", r.json()["settings"]["volume"] == 0.6)

# ---- Gender changes immediately ----
r = s.post(f"{BASE}/api/voice/settings", json={"gender": "female"})
r = s.get(f"{BASE}/api/voice/settings")
check("Gender change reflected immediately", r.json()["settings"]["gender"] == "female")

# ---- Persist after "refresh" (fresh GET request, same session) ----
r = s.get(f"{BASE}/api/voice/settings")
snap = r.json()["settings"]
check(
    "All four settings persist after a simulated page refresh",
    snap["provider"] == "edge" and snap["speed"] == 1.4 and snap["volume"] == 0.6 and snap["gender"] == "female",
    snap,
)

# ---- Persist after logout/login (real endpoints, fresh session) ----
r = s.post(f"{BASE}/api/logout")
check("Logout succeeds", r.ok and r.json()["success"])

s2 = requests.Session()  # a genuinely new session, like a new browser tab
r = s2.post(f"{BASE}/api/login", json={"phone": "0500000000", "password": "12345"})
check("Re-login succeeds", r.ok and r.json()["success"])

r = s2.get(f"{BASE}/api/voice/settings")
snap2 = r.json()["settings"]
check(
    "All four settings persist after logout + fresh login (new session)",
    snap2["provider"] == "edge" and snap2["speed"] == 1.4 and snap2["volume"] == 0.6 and snap2["gender"] == "female",
    snap2,
)

# ---- Reset back to defaults for subsequent tests ----
s2.post(f"{BASE}/api/voice/settings", json={"provider": "auto", "speed": 1.0, "volume": 1.0, "gender": "male"})

print("\n=== SUMMARY ===")
print("ALL PASS" if all(c for _, c in results) else "SOME FAILED")
