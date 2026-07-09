#!/usr/bin/python3
"""
Simulates a real WebAuthn platform authenticator (like Face ID / Touch ID)
against the live app_server.py, to prove whether register->verify and
login->verify actually work end-to-end with a genuine EC256 keypair and a
byte-correct authenticatorData/clientDataJSON, exactly like a browser would
produce.

This is a *test client*, not part of the app. It talks to the real Flask
process over HTTP so nothing about the server is mocked.
"""
import base64
import hashlib
import json
import uuid

import requests
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization

BASE = "http://127.0.0.1:5001"


def b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64url_decode(s: str) -> bytes:
    s = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode())


def build_authenticator_data(rp_id: str, sign_count: int = 1) -> bytes:
    rp_id_hash = hashlib.sha256(rp_id.encode()).digest()
    flags = bytes([0x05])  # UP (0x01) + UV (0x04) set, matches a real biometric unlock
    counter = sign_count.to_bytes(4, "big")
    return rp_id_hash + flags + counter


def sign_assertion(private_key, authenticator_data: bytes, client_data_json: bytes) -> bytes:
    signed_data = authenticator_data + hashlib.sha256(client_data_json).digest()
    return private_key.sign(signed_data, ec.ECDSA(hashes.SHA256()))


class FakeAuthenticator:
    def __init__(self):
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = b64url_encode(uuid.uuid4().bytes)

    def public_key_der_b64(self):
        pub = self.private_key.public_key()
        der = pub.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return base64.b64encode(der).decode()


def main():
    session = requests.Session()
    results = []

    def record(name, ok, detail=""):
        results.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")

    # 1. Log in normally with PIN first (webauthn register requires login)
    r = session.post(f"{BASE}/api/login", json={"phone": "0500000000", "password": "12345"})
    record("password login", r.ok and r.json().get("success"), r.text[:200])

    auth = FakeAuthenticator()

    # 2. Get registration options
    r = session.post(f"{BASE}/api/webauthn/register/options", json={})
    if not r.ok:
        record("register/options", False, r.text[:300])
        return
    reg_options = r.json()
    rp_id = reg_options["rp"]["id"]
    challenge_b64url = reg_options["challenge"]
    record("register/options", True, f"rp_id={rp_id}")

    # 3. Build a fake "credential.create()" response like the browser would
    client_data_create = json.dumps({
        "type": "webauthn.create",
        "challenge": challenge_b64url,
        "origin": BASE,
    }).encode()

    reg_payload = {
        "id": auth.credential_id,
        "clientDataJSON": b64url_encode(client_data_create),
        "publicKey": auth.public_key_der_b64(),
        "alg": -7,  # ES256
    }
    r = session.post(f"{BASE}/api/webauthn/register/verify", json=reg_payload)
    record("register/verify", r.ok and r.json().get("success"), r.text[:300])
    if not (r.ok and r.json().get("success")):
        return

    # ---- Now simulate a brand NEW session (like reopening the browser / a
    # fresh login attempt), which is the realistic case for "login fails
    # after enrollment succeeded". Use a fresh cookie jar.
    session2 = requests.Session()

    r = session2.post(f"{BASE}/api/webauthn/login/options", json={})
    if not r.ok:
        record("login/options", False, r.text[:300])
        return
    login_options = r.json()
    login_challenge_b64url = login_options["challenge"]
    record("login/options", True, f"allowCredentials id matches: {login_options['allowCredentials'][0]['id'] == auth.credential_id}")

    authenticator_data = build_authenticator_data(rp_id, sign_count=1)
    client_data_get = json.dumps({
        "type": "webauthn.get",
        "challenge": login_challenge_b64url,
        "origin": BASE,
    }).encode()

    signature = sign_assertion(auth.private_key, authenticator_data, client_data_get)

    login_payload = {
        "id": auth.credential_id,
        "clientDataJSON": b64url_encode(client_data_get),
        "authenticatorData": b64url_encode(authenticator_data),
        "signature": b64url_encode(signature),
    }
    r = session2.post(f"{BASE}/api/webauthn/login/verify", json=login_payload)
    record("login/verify (fresh session)", r.ok and r.json().get("success"), r.text[:300])

    # ---- Also test the ACTION webauthn gate (transfer confirmation), which
    # uses a separate challenge key (action_webauthn_challenge) and requires
    # a pending_action to exist first.
    r = session.post(f"{BASE}/api/action/create", json={
        "type": "transfer", "payload": {"beneficiary": "أحمد", "amount": 10}
    })
    record("action/create (transfer)", r.ok and r.json().get("success"), r.text[:300])

    r = session.post(f"{BASE}/api/action/webauthn/options", json={})
    if not r.ok:
        record("action/webauthn/options", False, r.text[:300])
        return
    action_options = r.json()
    action_challenge_b64url = action_options["challenge"]
    record("action/webauthn/options", True, "")

    authenticator_data2 = build_authenticator_data(rp_id, sign_count=2)
    client_data_get2 = json.dumps({
        "type": "webauthn.get",
        "challenge": action_challenge_b64url,
        "origin": BASE,
    }).encode()
    signature2 = sign_assertion(auth.private_key, authenticator_data2, client_data_get2)

    action_payload = {
        "id": auth.credential_id,
        "clientDataJSON": b64url_encode(client_data_get2),
        "authenticatorData": b64url_encode(authenticator_data2),
        "signature": b64url_encode(signature2),
    }
    r = session.post(f"{BASE}/api/action/webauthn/verify", json=action_payload)
    record("action/webauthn/verify (transfer confirm)", r.ok and r.json().get("success"), r.text[:300])

    print("\n=== SUMMARY ===")
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")


if __name__ == "__main__":
    main()
