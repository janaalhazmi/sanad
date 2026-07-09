#!/usr/bin/python3
"""Section 1 of the acceptance test: existing functionality, end to end,
through the real running server (not unit tests)."""
import json
import requests

BASE = "http://127.0.0.1:5001"
s = requests.Session()
results = []


def check(name, cond, detail=""):
    results.append((name, cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


# ---- Login ----
r = s.post(f"{BASE}/api/login", json={"phone": "0500000000", "password": "12345"})
check("Login succeeds", r.ok and r.json()["success"])

# ---- All pages load ----
for page in ["/dashboard", "/assistant", "/settings", "/cards", "/transfer",
             "/beneficiaries-page", "/transactions-page", "/notifications-page"]:
    r = s.get(f"{BASE}{page}")
    check(f"Page {page} loads (200)", r.status_code == 200)

# ---- Dashboard shows real balance ----
before = json.load(open("account.json", encoding="utf-8"))
r = s.get(f"{BASE}/dashboard")
check("Dashboard shows current balance", f"{before['balance']:.2f}" in r.text)

# ---- Real end-to-end transfer through the Transfer page's own API + OTP ----
r = s.post(f"{BASE}/api/action/create", json={
    "type": "transfer", "payload": {"beneficiary": "أحمد", "amount": 25}
})
check("Transfer action created (pending, gated)", r.ok and r.json()["success"])

r = s.get(f"{BASE}/api/action/status")
check("Pending action visible via status endpoint", r.ok and r.json()["success"] and r.json()["type"] == "transfer")

r = s.post(f"{BASE}/api/action/otp/send")
demo_code = r.json().get("demo_code")
check("OTP sent (demo code returned for this sandbox)", r.ok and r.json()["success"] and demo_code)

r = s.post(f"{BASE}/api/action/otp/verify", json={"code": "000000"})
check("Wrong OTP code is rejected", r.status_code == 400 and not r.json()["success"])

r = s.post(f"{BASE}/api/action/otp/verify", json={"code": demo_code})
check("Correct OTP code executes the transfer", r.ok and r.json()["success"])

after = json.load(open("account.json", encoding="utf-8"))
check("Balance actually decreased by the transferred amount", abs((before["balance"] - 25) - after["balance"]) < 0.001,
      f"before={before['balance']} after={after['balance']}")

r = s.get(f"{BASE}/transactions-page")
check("New transaction appears on transactions page", "أحمد" in r.text)

# ---- Beneficiary add through the real gated flow ----
r = s.post(f"{BASE}/api/action/create", json={
    "type": "add_beneficiary", "payload": {"name": "زياد", "iban": "SA9999999999999999999999", "nickname": "زياد"}
})
check("Add-beneficiary action created (pending, gated)", r.ok and r.json()["success"])
r = s.post(f"{BASE}/api/action/otp/send")
demo_code2 = r.json().get("demo_code")
r = s.post(f"{BASE}/api/action/otp/verify", json={"code": demo_code2})
check("Beneficiary actually added after OTP", r.ok and r.json()["success"])

beneficiaries = json.load(open("beneficiaries.json", encoding="utf-8"))
check("New beneficiary present in beneficiaries.json", any(b["name"] == "زياد" for b in beneficiaries))

# ---- Delete it back out (existing, unrelated-to-voice feature) ----
r = s.post(f"{BASE}/api/beneficiaries/delete", json={"name": "زياد"})
check("Beneficiary delete works", r.ok and r.json()["success"])

# ---- Cards page toggle (client-only in this demo, just confirm page serves) ----
r = s.get(f"{BASE}/cards")
check("Cards page shows the enrolled card", "1234" in r.text)

# ---- Notifications mark-as-read ----
r = s.post(f"{BASE}/api/notifications/read")
check("Notifications mark-as-read works", r.ok and r.json()["success"])

# ---- Assistant text query (local NLU path, unrelated to AI/voice) ----
r = s.post(f"{BASE}/api/assistant", json={"message": "كم رصيدي", "context": {"page": "assistant"}})
check("Assistant answers a simple balance query", r.ok and "ريال" in r.json().get("response", ""))

print("\n=== SUMMARY ===")
print("ALL PASS" if all(c for _, c in results) else "SOME FAILED")
