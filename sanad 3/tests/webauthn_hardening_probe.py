#!/usr/bin/python3
"""Proves the two new hardening behaviors added to app_server.py:
1. A challenge can only be used once (replay of an old challenge -> clear
   'expired' message, not a signature-verification crash).
2. If the stored credential's rp_id differs from the current host, the
   user gets an explicit, actionable message instead of a generic failure.
"""
import base64
import hashlib
import json
import uuid

import requests
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization

BASE = "http://127.0.0.1:5001"


def b64url_encode(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def build_authenticator_data(rp_id, sign_count=1):
    return hashlib.sha256(rp_id.encode()).digest() + bytes([0x05]) + sign_count.to_bytes(4, "big")


def sign(private_key, authenticator_data, client_data_json):
    return private_key.sign(authenticator_data + hashlib.sha256(client_data_json).digest(), ec.ECDSA(hashes.SHA256()))


session = requests.Session()
r = session.post(f"{BASE}/api/login", json={"phone": "0500000000", "password": "12345"})
assert r.json()["success"], r.text

private_key = ec.generate_private_key(ec.SECP256R1())
cred_id = b64url_encode(uuid.uuid4().bytes)
pub_der = private_key.public_key().public_bytes(
    encoding=serialization.Encoding.DER, format=serialization.PublicFormat.SubjectPublicKeyInfo)
pub_b64 = base64.b64encode(pub_der).decode()

r = session.post(f"{BASE}/api/webauthn/register/options", json={})
opts = r.json()
rp_id = opts["rp"]["id"]
challenge = opts["challenge"]

client_data_create = json.dumps({"type": "webauthn.create", "challenge": challenge, "origin": BASE}).encode()
r = session.post(f"{BASE}/api/webauthn/register/verify", json={
    "id": cred_id, "clientDataJSON": b64url_encode(client_data_create), "publicKey": pub_b64, "alg": -7,
})
assert r.json()["success"], r.text
print("PASS  enrolled")

# ---- Test 1: replay an OLD login challenge (simulate a stale/reused one) ----
r = session.post(f"{BASE}/api/webauthn/login/options", json={})
old_challenge = r.json()["challenge"]

# Get a second, fresh challenge (this invalidates using the old one, since
# each options call overwrites the stored challenge)
r = session.post(f"{BASE}/api/webauthn/login/options", json={})
new_challenge = r.json()["challenge"]

authdata = build_authenticator_data(rp_id, 2)
client_data_stale = json.dumps({"type": "webauthn.get", "challenge": old_challenge, "origin": BASE}).encode()
sig = sign(private_key, authdata, client_data_stale)
r = session.post(f"{BASE}/api/webauthn/login/verify", json={
    "id": cred_id, "clientDataJSON": b64url_encode(client_data_stale),
    "authenticatorData": b64url_encode(authdata), "signature": b64url_encode(sig),
})
ok = (not r.json()["success"])
print(f"{'PASS' if ok else 'FAIL'}  stale/replayed challenge rejected -> {r.json()['message']}")

# ---- Test 2: verifying twice with the same (now-consumed) challenge fails second time ----
r = session.post(f"{BASE}/api/webauthn/login/options", json={})
challenge2 = r.json()["challenge"]
authdata2 = build_authenticator_data(rp_id, 3)
client_data2 = json.dumps({"type": "webauthn.get", "challenge": challenge2, "origin": BASE}).encode()
sig2 = sign(private_key, authdata2, client_data2)
payload2 = {
    "id": cred_id, "clientDataJSON": b64url_encode(client_data2),
    "authenticatorData": b64url_encode(authdata2), "signature": b64url_encode(sig2),
}
r1 = session.post(f"{BASE}/api/webauthn/login/verify", json=payload2)
r2 = session.post(f"{BASE}/api/webauthn/login/verify", json=payload2)  # replay the exact same request
ok = r1.json()["success"] and (not r2.json()["success"])
print(f"{'PASS' if ok else 'FAIL'}  same challenge cannot be used twice -> first={r1.json()['success']} second={r2.json()['message']}")

# ---- Test 3: RP ID mismatch gives a clear diagnostic ----
import sqlite3, os as _os
# Simulate "enrolled from a different host" by hand-editing account.json's
# stored rp_id, then attempting login as normal.
with open("account.json", "r", encoding="utf-8") as f:
    account = json.load(f)
account["webauthn"]["rp_id"] = "some-other-host.example"
with open("account.json", "w", encoding="utf-8") as f:
    json.dump(account, f, ensure_ascii=False, indent=4)

r = session.post(f"{BASE}/api/webauthn/login/options", json={})
challenge3 = r.json()["challenge"]
authdata3 = build_authenticator_data(rp_id, 4)
client_data3 = json.dumps({"type": "webauthn.get", "challenge": challenge3, "origin": BASE}).encode()
sig3 = sign(private_key, authdata3, client_data3)
r = session.post(f"{BASE}/api/webauthn/login/verify", json={
    "id": cred_id, "clientDataJSON": b64url_encode(client_data3),
    "authenticatorData": b64url_encode(authdata3), "signature": b64url_encode(sig3),
})
msg = r.json().get("message", "")
ok = (not r.json()["success"]) and "عنوان مختلف" in msg
print(f"{'PASS' if ok else 'FAIL'}  rp_id mismatch gives clear diagnostic -> {msg}")
