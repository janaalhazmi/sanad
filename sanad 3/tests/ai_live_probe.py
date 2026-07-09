#!/usr/bin/python3
"""
Live, end-to-end probe for the OpenAI integration — run this against a
REAL running server (`python app_server.py` in another terminal) with a
REAL OPENAI_API_KEY configured. Unlike the unit-style tests in this folder
that mock ai_engine.get_ai_reply(), this makes actual network calls to
OpenAI and prints the literal result, so you get a definitive yes/no
instead of re-reading code.

Usage:
    python app_server.py                 # in one terminal
    python tests/ai_live_probe.py         # in another

What it checks:
1. /health reports ai_diagnostics with module_loaded / openai_package_installed
   / openai_api_key_present all true.
2. /api/ai/diagnose places one real OpenAI Responses API call and gets a
   real answer back (proves the network/auth/model path works at all).
3. A real open-ended banking question ("كيف أطلع قرض؟") sent to
   /api/assistant returns a genuine AI-generated answer — not the local
   NLU's generic "لم أفهم" fallback text, and no incorrect page navigation.
"""
import sys
import requests

BASE = "http://127.0.0.1:5001"
GENERIC_FALLBACK_TEXT = "عذراً، لم أفهم طلبك"

session = requests.Session()
results = []


def check(name, condition, detail=""):
    results.append((name, condition))
    print(f"{'PASS' if condition else 'FAIL'}  {name}  {detail}")


def login():
    r = session.post(f"{BASE}/api/login", json={"phone": "0500000000", "password": "12345"})
    ok = r.status_code == 200 and r.json().get("success")
    check("login succeeds", ok, r.text[:120])
    return ok


# ---- 1. /health config presence ----
r = session.get(f"{BASE}/health")
health = r.json() if r.status_code == 200 else {}
diag = health.get("ai_diagnostics", {})
check("health: module_loaded", diag.get("module_loaded") is True, diag)
check("health: openai_package_installed", diag.get("openai_package_installed") is True, diag)
check("health: openai_api_key_present", diag.get("openai_api_key_present") is True, diag)

if not all(c for _, c in results):
    print("\nConfig-level check(s) failed above — fix those first (install `openai`, set OPENAI_API_KEY in .env) "
          "before the live network checks below can possibly pass.")

if not login():
    print("\nCannot continue without a logged-in session.")
    sys.exit(1)

# ---- 2. Real, live OpenAI call ----
r = session.get(f"{BASE}/api/ai/diagnose")
data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
check(
    "live OpenAI call succeeds (/api/ai/diagnose)",
    r.status_code == 200 and data.get("success") is True,
    data.get("message") or data,
)
if data.get("success"):
    print(f"      model used: {data.get('model')}, response text: {data.get('response_text')!r}, "
          f"elapsed: {data.get('elapsed_seconds')}s")

# ---- 3. Real assistant question through the full request pipeline ----
r = session.post(f"{BASE}/api/assistant", json={"message": "كيف أطلع قرض؟", "context": {"page": "assistant"}})
resp = r.json() if r.status_code == 200 else {}
response_text = resp.get("response", "")
action = resp.get("action")
check(
    "loan question does NOT hit the generic local fallback text",
    GENERIC_FALLBACK_TEXT not in response_text,
    response_text[:160],
)
check(
    "loan question gets a substantive answer (not a one-line local reply)",
    len(response_text) > 40,
    f"len={len(response_text)}",
)
check("loan question triggers NO page navigation", action is None, action)

print("\n=== SUMMARY ===")
all_pass = all(c for _, c in results)
for name, ok in results:
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
print("\nALL PASS — OpenAI integration confirmed working end-to-end." if all_pass else
      "\nSOME FAILED — see the FAIL lines above; each prints the real underlying reason.")
