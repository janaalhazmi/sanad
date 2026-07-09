#!/usr/bin/python3
"""
سَند - Sanad Digital Banking demo backend.

Every money-moving action (transfer, add beneficiary) goes through a single
mandatory authentication gate: /api/action/* + the /auth-verify page, which
offers real WebAuthn (fingerprint/Face ID) or SMS OTP. Nothing executes
until one of those two actually succeeds — see _execute_pending_action().
"""

from flask import (
    Flask, render_template, request, jsonify, session, redirect, url_for
)
import json
import re
import os
import sys
import base64
import hashlib
import time
import uuid
import threading

# Load a local .env file (OPENAI_API_KEY, SANAD_SECRET_KEY, etc.) into the
# process environment BEFORE anything below reads os.environ. Optional on
# purpose: in a real deployment the env vars are usually set by the host
# already, so a missing `python-dotenv` package or missing `.env` file must
# never crash the app -- it just means "no .env file to load", identical to
# how every other optional subsystem in this file degrades.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print(
        "NOTE: python-dotenv not installed — .env files won't be auto-loaded. "
        "Install it (`pip install python-dotenv`, already in requirements.txt) "
        "or export OPENAI_API_KEY/SANAD_SECRET_KEY directly in your shell.",
        file=sys.stderr,
    )

from otp import OTPManager
from transfer import TransferManager
from beneficiary import BeneficiaryManager
import assistant_nlu as nlu

# The OpenAI fallback layer is fully optional: if the `ai` package, the
# `openai` dependency, or the API key aren't available, ai_engine is left
# as None and the assistant behaves exactly as it did before this feature
# existed (local NLU only). This import must never be able to crash the app.
try:
    from ai import ai_engine
except Exception as _ai_import_error:
    ai_engine = None
    print(
        f"NOTE: AI fallback layer (ai/ai_engine.py) failed to import — "
        f"general Q&A will use the local NLU only. Reason: "
        f"{type(_ai_import_error).__name__}: {_ai_import_error}",
        file=sys.stderr,
    )

# The voice (TTS) subsystem is likewise fully optional: if the `voice`
# package or its underlying providers (edge-tts, openai) aren't available,
# voice_tts is left as None and /api/tts always reports the browser
# fallback, exactly matching the app's pre-existing client-side-only
# speechSynthesis behavior. This import must never be able to crash the app.
try:
    from voice import tts as voice_tts
except Exception:
    voice_tts = None

# Voice Authentication is likewise fully optional: if the `voice_auth`
# package or its underlying embedding providers (speechbrain, resemblyzer)
# aren't available, voice_auth_engine is left as None. Critically, this is
# NOT the same graceful-degradation policy as the AI/TTS layers above --
# see _complete_primary_auth() and /api/action/voice/verify below, which
# fail CLOSED (block the action) if a user has voice auth enabled but the
# engine is unavailable, rather than silently skipping the check.
try:
    from voice_auth import engine as voice_auth_engine
except Exception:
    voice_auth_engine = None

# Accessibility settings are pure data (no ML/network dependency), so this
# import cannot realistically fail -- guarded anyway for consistency with
# every other optional subsystem in this file.
try:
    from accessibility import engine as accessibility_engine
except Exception:
    accessibility_engine = None

app = Flask(__name__)

_DEMO_SECRET_KEY = "sanad-demo-secret-key-change-me"
app.secret_key = os.environ.get("SANAD_SECRET_KEY", _DEMO_SECRET_KEY)
if app.secret_key == _DEMO_SECRET_KEY:
    print(
        "WARNING: SANAD_SECRET_KEY is not set — using the built-in demo secret key. "
        "Sessions signed with this key are NOT secure for any real deployment. "
        "Set SANAD_SECRET_KEY to a random value before deploying beyond local demo use.",
        file=sys.stderr,
    )

# Static assets rarely change during a demo session; let the browser cache
# them instead of re-fetching on every navigation (real, measurable speed win).
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600

try:
    # Optional: only needed if the frontend is served from a different origin.
    from flask_cors import CORS
    CORS(app, supports_credentials=True)
except ImportError:
    pass


@app.before_request
def _redirect_ip_to_localhost():
    """WebAuthn platform authenticators (Touch ID / Face ID / Windows Hello /
    Android fingerprint) require a valid RP ID. Per spec, an IP address like
    127.0.0.1 is NEVER a valid RP ID -- only 'localhost' and real registrable
    domains are -- so opening the app via http://127.0.0.1:5001 makes
    fingerprint registration/login fail or throw on many browsers, even
    though everything else works identically. This transparently 307s any
    request on 127.0.0.1 (or ::1) to the equivalent localhost URL (same
    port/path/query/method/body), so WebAuthn always has a valid RP ID no
    matter which address the user happened to type in."""
    host = request.host  # e.g. "127.0.0.1:5001"
    hostname = host.split(":")[0]
    if hostname in ("127.0.0.1", "::1"):
        port_part = host.split(":", 1)[1] if ":" in host else ""
        new_host = "localhost" + (":" + port_part if port_part else "")
        new_url = request.url.replace(host, new_host, 1)
        return redirect(new_url, code=307)


# Bumped once per process start -- appended to static asset URLs as a
# cache-busting query string so a restarted server with updated CSS/JS
# is never masked by the browser's static-file cache
# (SEND_FILE_MAX_AGE_DEFAULT below caches aggressively on purpose for
# real demo performance; this is what makes "I changed the colors and
# restarted but the browser still shows the old ones" impossible).
_ASSET_VERSION = str(int(time.time()))


@app.context_processor
def _inject_asset_version():
    return {"asset_v": _ASSET_VERSION}

otp = OTPManager()
beneficiary = BeneficiaryManager()

ACCOUNT_FILE = "account.json"
BENEFICIARY_FILE = "beneficiaries.json"

# Load the ML intent model in the background at startup instead of on the
# first request, so real assistant replies aren't slowed down by it.
threading.Thread(target=nlu.warm_up, daemon=True).start()


# -----------------------------------------------------------------------
# Data helpers
# -----------------------------------------------------------------------
def load_account():
    with open(ACCOUNT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_account(account):
    with open(ACCOUNT_FILE, "w", encoding="utf-8") as f:
        json.dump(account, f, ensure_ascii=False, indent=4)


def load_beneficiaries():
    with open(BENEFICIARY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def get_transfer_manager():
    # Build fresh each call so it always reflects the latest account.json
    return TransferManager()


def is_logged_in():
    return bool(session.get("logged_in"))


def _require_login():
    if not is_logged_in():
        return jsonify(success=False, message="سجّل الدخول أولاً"), 401
    return None


@app.context_processor
def inject_accessibility_settings():
    """Makes `accessibility` AND `theme` available in EVERY template
    automatically. Extended in Step 6 to also carry the light/dark theme
    preference, computed from the same account load rather than a second
    one — server-rendered at request time, same no-flash-of-unstyled-
    content approach already used for Senior Mode/High Contrast."""
    try:
        if not is_logged_in():
            raise LookupError("not logged in")
        account = load_account()
        if accessibility_engine is not None:
            settings = accessibility_engine.get_settings(account)
        else:
            settings = {"senior_mode": False, "high_contrast": False}
        theme = account.get("theme", "dark")
        wake_word_enabled = bool(account.get("wake_word_enabled", False))
    except Exception:
        settings = {"senior_mode": False, "high_contrast": False}
        theme = "dark"
        wake_word_enabled = False
    return {"accessibility": settings, "theme": theme, "wake_word_enabled": wake_word_enabled}


@app.route("/api/theme", methods=["GET"])
def api_theme_get():
    guard = _require_login()
    if guard:
        return guard
    account = load_account()
    return jsonify(success=True, theme=account.get("theme", "dark"))


@app.route("/api/theme", methods=["POST"])
def api_theme_update():
    guard = _require_login()
    if guard:
        return guard
    data = request.json or {}
    theme = str(data.get("theme", "")).strip().lower()
    if theme not in ("light", "dark"):
        return jsonify(success=False, message="قيمة غير صحيحة للمظهر (light أو dark فقط)"), 400
    account = load_account()
    account["theme"] = theme
    save_account(account)
    return jsonify(success=True, message="تم تغيير المظهر", theme=theme)


# -----------------------------------------------------------------------
# Wake Word — "Hey Sanad" / "يا سند". Entirely client-side detection (see
# static/js/wake_word.js); the server only persists the on/off preference,
# exactly like /api/theme. Enabling this never bypasses or changes how
# commands are processed once heard — it only decides whether the existing
# mic button gets triggered automatically after the phrase is detected.
# -----------------------------------------------------------------------
@app.route("/api/wake-word/settings", methods=["GET"])
def api_wake_word_get():
    guard = _require_login()
    if guard:
        return guard
    account = load_account()
    return jsonify(success=True, enabled=bool(account.get("wake_word_enabled", False)))


@app.route("/api/wake-word/settings", methods=["POST"])
def api_wake_word_update():
    guard = _require_login()
    if guard:
        return guard
    data = request.json or {}
    account = load_account()
    account["wake_word_enabled"] = bool(data.get("enabled"))
    save_account(account)
    return jsonify(success=True, message="تم حفظ إعداد كلمة التنبيه", enabled=account["wake_word_enabled"])


# -----------------------------------------------------------------------
# WebAuthn (Face ID / Touch ID / Windows Hello / Android biometrics) helpers
# -----------------------------------------------------------------------
# Real platform-authenticator biometric auth. The private key never leaves
# the device's secure hardware (Secure Enclave / TPM / Keystore); we only
# ever see a public key, which is what makes WebAuthn safe to use this way.
def _b64url_decode(s):
    s = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode())


def _b64url_encode(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _rp_id():
    return request.host.split(":")[0]


def _expected_origin():
    return request.host_url.rstrip("/")


def _verify_client_data(client_data_json_bytes, expected_type, expected_challenge_b64url):
    try:
        client_data = json.loads(client_data_json_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False, "بيانات غير صالحة"

    if client_data.get("type") != expected_type:
        return False, "نوع الطلب غير متطابق"
    if client_data.get("challenge") != expected_challenge_b64url:
        return False, "انتهت صلاحية الطلب، حاول مرة أخرى"
    origin = client_data.get("origin", "")
    if origin.rstrip("/") != _expected_origin():
        return False, "مصدر الطلب غير موثوق"
    return True, None


def _verify_webauthn_assertion(data, expected_challenge, cred):
    """Shared, real signature-verification core used by both login and the
    action-verification gate. Returns (ok: bool, message: str|None)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, padding
    from cryptography.exceptions import InvalidSignature

    if not cred or data.get("id") != cred.get("id"):
        return False, "بيانات اعتماد غير معروفة على هذا الحساب"

    if not expected_challenge:
        return False, "انتهت صلاحية الطلب، حاول مرة أخرى"

    try:
        client_data_bytes = _b64url_decode(data["clientDataJSON"])
        authenticator_data = _b64url_decode(data["authenticatorData"])
        signature = _b64url_decode(data["signature"])
    except Exception:
        return False, "بيانات غير صالحة"

    ok, err = _verify_client_data(client_data_bytes, "webauthn.get", expected_challenge)
    if not ok:
        return False, err

    # authenticatorData layout: rpIdHash(32) + flags(1) + signCounter(4) [+ extras]
    if len(authenticator_data) < 37:
        return False, "بيانات المصادقة غير صالحة"

    rp_id_hash = authenticator_data[:32]
    if rp_id_hash != hashlib.sha256(_rp_id().encode()).digest():
        return False, "جهة الطلب غير متطابقة"

    flags = authenticator_data[32]
    user_present = bool(flags & 0x01)
    user_verified = bool(flags & 0x04)
    if not (user_present and user_verified):
        return False, "لم يتم التحقق من هوية المستخدم بالبصمة"

    try:
        public_key_bytes = base64.b64decode(cred["public_key"])
        public_key = serialization.load_der_public_key(public_key_bytes)
        signed_data = authenticator_data + hashlib.sha256(client_data_bytes).digest()
        alg = cred.get("alg")

        if alg == -7:  # ES256
            public_key.verify(signature, signed_data, ec.ECDSA(hashes.SHA256()))
        elif alg == -257:  # RS256
            public_key.verify(signature, signed_data, padding.PKCS1v15(), hashes.SHA256())
        else:
            return False, "خوارزمية غير مدعومة"
    except InvalidSignature:
        return False, "فشل التحقق من التوقيع"
    except Exception:
        return False, "تعذر التحقق من البصمة"

    return True, None


# -----------------------------------------------------------------------
# Central authentication gate for sensitive actions (transfer, add
# beneficiary). Nothing here ever "fakes" success — the action is only
# executed inside _execute_pending_action(), which is only reachable after
# a real OTP match or a real WebAuthn signature check.
# -----------------------------------------------------------------------
PENDING_ACTION_TTL_SECONDS = 10 * 60


def _pending_action():
    pending = session.get("pending_action")
    if not pending:
        return None
    if time.time() - pending.get("created", 0) > PENDING_ACTION_TTL_SECONDS:
        session.pop("pending_action", None)
        return None
    return pending


def _describe_pending_action(pending):
    payload = pending["payload"]
    if pending["type"] == "transfer":
        return f"تحويل {payload['amount']:g} ريال إلى {payload['beneficiary']}"
    if pending["type"] == "add_beneficiary":
        return f"إضافة مستفيد جديد: {payload['name']}"
    return "عملية غير معروفة"


def _create_pending_action(action_type, payload):
    """Validates the request (e.g. 'verify beneficiary') and stores it as a
    pending action awaiting authentication. Returns (ok, message, token)."""
    beneficiaries = load_beneficiaries()

    if action_type == "transfer":
        name_hint = str(payload.get("beneficiary") or "").strip()
        ben = nlu.find_beneficiary(beneficiaries, name_hint)
        if not ben:
            return False, "المستفيد غير موجود، تحقق من الاسم أو أضفه أولاً", None

        try:
            amount = float(payload.get("amount"))
        except (TypeError, ValueError):
            return False, "المبلغ غير صحيح", None
        if amount <= 0:
            return False, "المبلغ غير صحيح", None

        account = load_account()
        if amount > account["balance"]:
            return False, "رصيدك غير كافٍ", None

        resolved_payload = {"beneficiary": ben["name"], "amount": amount}

    elif action_type == "add_beneficiary":
        name = str(payload.get("name") or "").strip()
        iban = str(payload.get("iban") or "").strip()
        nickname = str(payload.get("nickname") or "").strip() or name
        if not name or not iban:
            return False, "الاسم ورقم الآيبان مطلوبان", None

        beneficiary.load()
        if beneficiary.exists(name):
            return False, "هذا المستفيد مضاف مسبقاً", None

        resolved_payload = {"name": name, "iban": iban, "nickname": nickname}

    else:
        return False, "نوع عملية غير معروف", None

    token = uuid.uuid4().hex
    session["pending_action"] = {
        "token": token,
        "type": action_type,
        "payload": resolved_payload,
        "created": time.time(),
    }
    return True, "تم التحقق من الطلب، الرجاء إكمال المصادقة", token


def _execute_pending_action():
    """Actually perform the action. Only ever called after a real OTP match
    or a real WebAuthn signature verification succeeded."""
    pending = _pending_action()
    if not pending:
        return {"success": False, "message": "لا يوجد طلب معلّق أو انتهت صلاحيته"}

    session.pop("pending_action", None)
    payload = pending["payload"]

    if pending["type"] == "transfer":
        tm = get_transfer_manager()
        result = tm.transfer(payload["beneficiary"], payload["amount"])
        if result.get("success"):
            result["redirect"] = url_for("dashboard")
        return result

    if pending["type"] == "add_beneficiary":
        beneficiary.load()
        added = beneficiary.add(payload["name"], payload["iban"], payload["nickname"])
        if added:
            return {
                "success": True,
                "message": "تمت إضافة المستفيد بنجاح",
                "redirect": url_for("beneficiaries_page"),
            }
        return {"success": False, "message": "تعذر إضافة المستفيد (موجود مسبقاً)"}

    return {"success": False, "message": "نوع عملية غير معروف"}


def _complete_primary_auth():
    """Called immediately after OTP or WebAuthn primary verification
    succeeds — the ONLY two places that used to call
    _execute_pending_action() directly now call this instead.

    Default (unchanged) behavior: if the account does NOT have Voice
    Authentication enabled, this executes the action immediately, exactly
    as before this feature existed — byte-for-byte the same code path.

    New behavior: if Voice Authentication IS enabled, this does NOT
    execute yet. It marks the pending action as primary-verified (so
    /api/action/voice/verify can confirm that OTP/WebAuthn genuinely
    succeeded for THIS specific pending action before accepting a voice
    sample) and tells the client one more step is required.

    Returns (response_dict, http_status) — callers just do
    `return jsonify(**result), status`."""
    pending = _pending_action()
    if not pending:
        return {"success": False, "message": "لا يوجد طلب معلّق أو انتهت صلاحيته"}, 400

    account = load_account()
    if voice_auth_engine is not None and voice_auth_engine.is_enabled(account):
        pending["primary_verified"] = True
        session["pending_action"] = pending
        return {
            "success": True,
            "requires_voice": True,
            "message": "تم التحقق الأساسي بنجاح، الرجاء تسجيل صوتك لإتمام العملية",
        }, 200

    result = _execute_pending_action()
    status = 200 if result.get("success") else 400
    return result, status


# -----------------------------------------------------------------------
# Page routes
# -----------------------------------------------------------------------
# -----------------------------------------------------------------------
# Emergency security lockout — triggered ONLY by the assistant's emergency-
# phrase detection (see _resolve_locally() below). In-memory and keyed by
# phone since this is a single-process demo server with one account; a
# production deployment would persist this per-account server-side instead.
# Deliberately NOT tied to the session (which gets cleared as part of the
# same emergency response) — the lockout must survive the forced logout and
# block EVERY login path (PIN/password, quick/biometric) until it expires.
# -----------------------------------------------------------------------
EMERGENCY_LOCKOUT_SECONDS = 30
_emergency_lockouts = {}  # phone -> unlock_at (epoch seconds)


def _trigger_emergency_lockout(phone: str) -> None:
    _emergency_lockouts[phone or "default"] = time.time() + EMERGENCY_LOCKOUT_SECONDS


def _lockout_remaining(phone: str) -> int:
    unlock_at = _emergency_lockouts.get(phone or "default")
    if not unlock_at:
        return 0
    remaining = unlock_at - time.time()
    return max(0, int(remaining + 0.999))  # round up to whole seconds


@app.route("/")
def login():
    if is_logged_in():
        return redirect(url_for("dashboard"))
    account = load_account()
    lockout_seconds = _lockout_remaining(account.get("phone") or "default")
    return render_template("login.html", account=account, lockout_seconds=lockout_seconds)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
def dashboard():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("dashboard.html", account=load_account())


@app.route("/cards")
def cards():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("cards.html", account=load_account())


@app.route("/transfer")
def transfer_page():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template(
        "transfer.html",
        account=load_account(),
        beneficiaries=load_beneficiaries(),
    )


@app.route("/beneficiaries-page")
def beneficiaries_page():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("beneficiaries.html", beneficiaries=load_beneficiaries())


@app.route("/assistant")
def assistant():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("assistant.html", account=load_account())


@app.route("/transactions-page")
def transactions_page():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("transactions.html", account=load_account())


@app.route("/notifications-page")
def notifications_page():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("notifications.html", account=load_account())


@app.route("/settings")
def settings():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("settings.html", account=load_account())


@app.route("/auth-verify")
def auth_verify_page():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("auth_verify.html", account=load_account())


# -----------------------------------------------------------------------
# Auth API
# -----------------------------------------------------------------------
@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.json or {}
    phone = str(data.get("phone", "")).strip()
    password = str(data.get("password", "")).strip()

    account = load_account()

    remaining = _lockout_remaining(phone or account.get("phone") or "default")
    if remaining > 0:
        return jsonify(
            success=False,
            locked=True,
            remaining=remaining,
            message=f"تم إيقاف تسجيل الدخول مؤقتاً لأسباب أمنية. حاول مرة أخرى خلال {remaining} ثانية.",
        ), 423

    if password and (not phone or phone == account.get("phone")) and password == account.get("password"):
        session["logged_in"] = True
        session["phone"] = account.get("phone")
        return jsonify(success=True, message="تم تسجيل الدخول بنجاح", redirect=url_for("dashboard"))

    return jsonify(success=False, message="رقم الجوال أو كلمة المرور غير صحيحة"), 401


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify(success=True)


# -----------------------------------------------------------------------
# WebAuthn challenge store: adds an explicit TTL (mirrors PENDING_ACTION_TTL)
# and always-clear-after-one-attempt semantics on top of the three
# session-scoped challenges (register / login / action), so a stale or
# already-used challenge can never be replayed and always gives a clear
# "انتهت صلاحية الطلب" message instead of a confusing signature failure.
# -----------------------------------------------------------------------
WEBAUTHN_CHALLENGE_TTL_SECONDS = 60  # matches the `timeout` sent to the client


def _set_webauthn_challenge(session_key, challenge_b64url, rp_id):
    session[session_key] = {
        "challenge": challenge_b64url,
        "rp_id": rp_id,
        "created": time.time(),
    }


def _pop_webauthn_challenge(session_key):
    """Fetch-and-clear: valid only once, and only within the TTL. Returns
    (challenge_b64url, rp_id) or (None, None)."""
    entry = session.pop(session_key, None)
    if not entry:
        return None, None
    if time.time() - entry.get("created", 0) > WEBAUTHN_CHALLENGE_TTL_SECONDS:
        return None, None
    return entry.get("challenge"), entry.get("rp_id")


# -----------------------------------------------------------------------
# WebAuthn (Face ID / Touch ID / Windows Hello / fingerprint) — LOGIN
# -----------------------------------------------------------------------
@app.route("/api/webauthn/status")
def api_webauthn_status():
    account = load_account()
    return jsonify(enabled=bool(account.get("webauthn")))


@app.route("/api/webauthn/register/options", methods=["POST"])
def api_webauthn_register_options():
    guard = _require_login()
    if guard:
        return guard

    account = load_account()
    challenge = os.urandom(32)
    _set_webauthn_challenge("webauthn_reg_challenge", _b64url_encode(challenge), _rp_id())

    user_id = _b64url_encode(hashlib.sha256(account.get("phone", account["name"]).encode()).digest())

    options = {
        "challenge": _b64url_encode(challenge),
        "rp": {"name": "سَند", "id": _rp_id()},
        "user": {
            "id": user_id,
            "name": account.get("phone", account["name"]),
            "displayName": account["name"],
        },
        "pubKeyCredParams": [
            {"type": "public-key", "alg": -7},    # ES256
            {"type": "public-key", "alg": -257},  # RS256
        ],
        "authenticatorSelection": {
            "authenticatorAttachment": "platform",
            "userVerification": "required",
            "residentKey": "preferred",
        },
        "timeout": 60000,
        "attestation": "none",
    }
    return jsonify(options)


@app.route("/api/webauthn/register/verify", methods=["POST"])
def api_webauthn_register_verify():
    guard = _require_login()
    if guard:
        return guard

    data = request.json or {}
    expected_challenge, rp_id_at_registration = _pop_webauthn_challenge("webauthn_reg_challenge")
    if not expected_challenge:
        return jsonify(success=False, message="انتهت صلاحية الطلب، حاول مرة أخرى"), 400

    try:
        client_data_bytes = _b64url_decode(data["clientDataJSON"])
    except Exception:
        return jsonify(success=False, message="بيانات غير صالحة"), 400

    ok, err = _verify_client_data(client_data_bytes, "webauthn.create", expected_challenge)
    if not ok:
        return jsonify(success=False, message=err), 400

    # Extract the public key OURSELVES from the raw attestationObject,
    # instead of trusting the browser's optional Level-3 convenience methods
    # (credential.response.getPublicKey()/getPublicKeyAlgorithm()). Those
    # are NOT implemented on every real device (several iOS Safari
    # versions, in-app webviews, older Android WebViews return undefined),
    # which used to make registration fail outright on those devices with
    # no way to recover. attestationObject itself is part of the original
    # WebAuthn spec and always present — parsing it here fixes fingerprint
    # enrollment across the full range of supported browsers/devices.
    from webauthn_cbor import parse_attestation_object, cose_key_to_der_public_key, AuthDataParseError

    try:
        attestation_object = _b64url_decode(data["attestationObject"])
    except Exception:
        return jsonify(success=False, message="بيانات المصادقة (attestationObject) مفقودة أو غير صالحة"), 400

    try:
        parsed = parse_attestation_object(attestation_object)
        auth_data = parsed["parsed_auth_data"]
    except AuthDataParseError as e:
        return jsonify(success=False, message=f"تعذر قراءة بيانات المصادقة: {e}"), 400
    except Exception as e:
        return jsonify(success=False, message="تعذر قراءة بيانات المصادقة"), 400

    if auth_data["rp_id_hash"] != hashlib.sha256(_rp_id().encode()).digest():
        return jsonify(success=False, message="جهة الطلب غير متطابقة"), 400

    flags = auth_data["flags"]
    if not (flags & 0x01):  # user present
        return jsonify(success=False, message="لم يتم التحقق من وجود المستخدم"), 400
    if not (flags & 0x04):  # user verified (actual biometric/PIN check)
        return jsonify(success=False, message="لم يتم التحقق من هوية المستخدم بالبصمة"), 400

    if not auth_data["credential_public_key"]:
        return jsonify(success=False, message="لم يتم إرسال مفتاح عام صالح من الجهاز"), 400

    try:
        public_key_der, alg = cose_key_to_der_public_key(auth_data["credential_public_key"])
    except AuthDataParseError as e:
        return jsonify(success=False, message=f"نوع المفتاح غير مدعوم: {e}"), 400

    # credential.id (base64url of rawId) is sent by the client for
    # convenience/consistency with the login/action flows, but we verify it
    # against the credentialId embedded in the signed authData rather than
    # trusting it blindly.
    derived_id = _b64url_encode(auth_data["credential_id"]) if auth_data["credential_id"] else None
    client_sent_id = data.get("id")
    if derived_id and client_sent_id and derived_id != client_sent_id:
        return jsonify(success=False, message="عدم تطابق في معرّف بيانات الاعتماد"), 400

    account = load_account()
    account["webauthn"] = {
        "id": client_sent_id or derived_id,
        "public_key": base64.b64encode(public_key_der).decode(),
        "alg": alg,
        # Recorded so a later login/action attempt from a different host
        # (localhost vs. 127.0.0.1 vs. a LAN IP/tunnel — the #1 real-world
        # cause of "enrolled fine, verification fails") gets a clear,
        # actionable message instead of a cryptic signature mismatch.
        "rp_id": rp_id_at_registration,
    }
    save_account(account)

    return jsonify(success=True, message="تم تفعيل الدخول بالبصمة على هذا الجهاز")


@app.route("/api/webauthn/login/options", methods=["POST"])
def api_webauthn_login_options():
    account = load_account()
    remaining = _lockout_remaining(account.get("phone") or "default")
    if remaining > 0:
        return jsonify(success=False, locked=True, remaining=remaining, message="تم إيقاف تسجيل الدخول مؤقتاً لأسباب أمنية."), 423
    cred = account.get("webauthn")
    if not cred:
        return jsonify(success=False, message="الدخول بالبصمة غير مفعّل"), 404

    challenge = os.urandom(32)
    _set_webauthn_challenge("webauthn_login_challenge", _b64url_encode(challenge), _rp_id())

    options = {
        "challenge": _b64url_encode(challenge),
        "rpId": _rp_id(),
        "allowCredentials": [{"id": cred["id"], "type": "public-key", "transports": ["internal"]}],
        "userVerification": "required",
        "timeout": 60000,
    }
    return jsonify(options)


@app.route("/api/webauthn/login/verify", methods=["POST"])
def api_webauthn_login_verify():
    data = request.json or {}
    account = load_account()
    remaining = _lockout_remaining(account.get("phone") or "default")
    if remaining > 0:
        return jsonify(success=False, locked=True, remaining=remaining, message="تم إيقاف تسجيل الدخول مؤقتاً لأسباب أمنية."), 423
    cred = account.get("webauthn")
    expected_challenge, rp_id_at_challenge_time = _pop_webauthn_challenge("webauthn_login_challenge")

    if not expected_challenge:
        return jsonify(success=False, message="انتهت صلاحية الطلب، حاول مرة أخرى"), 400

    enrolled_rp_id = cred.get("rp_id") if cred else None
    if enrolled_rp_id and enrolled_rp_id != _rp_id():
        # Enrolled from a different hostname than the one being used now
        # (e.g. localhost vs. a LAN IP) — WebAuthn credentials are bound to
        # the RP ID at creation time, so this can never succeed here. Tell
        # the user exactly what to do instead of a generic failure.
        return jsonify(
            success=False,
            message=f"تم تفعيل البصمة على عنوان مختلف ({enrolled_rp_id}). افتح التطبيق من نفس العنوان أو أعد تفعيل البصمة من الإعدادات.",
        ), 400

    ok, err = _verify_webauthn_assertion(data, expected_challenge, cred)
    if not ok:
        return jsonify(success=False, message=err), 400

    session["logged_in"] = True
    session["phone"] = account.get("phone")

    return jsonify(success=True, message="تم تسجيل الدخول بالبصمة", redirect=url_for("dashboard"))


@app.route("/api/webauthn/disable", methods=["POST"])
def api_webauthn_disable():
    guard = _require_login()
    if guard:
        return guard

    account = load_account()
    account.pop("webauthn", None)
    save_account(account)
    return jsonify(success=True, message="تم إيقاف الدخول بالبصمة")


# -----------------------------------------------------------------------
# Action gate API — used by Transfer, Beneficiaries, and the assistant for
# EVERY sensitive action. This is the only path that can move money or
# create a beneficiary.
# -----------------------------------------------------------------------
@app.route("/api/action/create", methods=["POST"])
def api_action_create():
    guard = _require_login()
    if guard:
        return guard

    data = request.json or {}
    action_type = data.get("type")
    payload = data.get("payload") or {}

    if action_type not in ("transfer", "add_beneficiary"):
        return jsonify(success=False, message="نوع عملية غير معروف"), 400

    ok, message, token = _create_pending_action(action_type, payload)
    if not ok:
        return jsonify(success=False, message=message), 400

    account = load_account()
    return jsonify(
        success=True,
        message=message,
        token=token,
        webauthn_available=bool(account.get("webauthn")),
    )


@app.route("/api/action/status")
def api_action_status():
    guard = _require_login()
    if guard:
        return guard

    pending = _pending_action()
    if not pending:
        return jsonify(success=False, message="لا يوجد طلب معلّق"), 404

    account = load_account()
    voice_auth_enabled = bool(voice_auth_engine is not None and voice_auth_engine.is_enabled(account))
    return jsonify(
        success=True,
        type=pending["type"],
        description=_describe_pending_action(pending),
        webauthn_available=bool(account.get("webauthn")),
        voice_auth_required=voice_auth_enabled,
        primary_verified=bool(pending.get("primary_verified")),
    )


@app.route("/api/action/cancel", methods=["POST"])
def api_action_cancel():
    guard = _require_login()
    if guard:
        return guard
    session.pop("pending_action", None)
    return jsonify(success=True, message="تم إلغاء العملية")


@app.route("/api/action/otp/send", methods=["POST"])
def api_action_otp_send():
    guard = _require_login()
    if guard:
        return guard
    if not _pending_action():
        return jsonify(success=False, message="لا يوجد طلب معلّق"), 404

    account = load_account()
    otp.send_code(account.get("phone", ""))
    # There is no real SMS gateway in this demo, so the code is echoed back
    # to the client so the flow can be completed end-to-end.
    return jsonify(success=True, message="تم إرسال رمز التحقق", demo_code=otp.code)


@app.route("/api/action/otp/verify", methods=["POST"])
def api_action_otp_verify():
    guard = _require_login()
    if guard:
        return guard
    if not _pending_action():
        return jsonify(success=False, message="لا يوجد طلب معلّق"), 404

    raw_code = str((request.json or {}).get("code", "")).strip()
    # Accept digits (Arabic-Indic or ASCII) OR a spoken digit sequence like
    # "واحد اثنين ثلاثة أربعة خمسة ستة".
    code = re.sub(r"\D", "", nlu._normalize_digits(raw_code))
    if len(code) != 6:
        spoken = nlu.words_to_digit_string(raw_code)
        if spoken:
            code = spoken

    if len(code) != 6 or not otp.verify(code):
        return jsonify(success=False, message="رمز التحقق غير صحيح"), 400

    result, status = _complete_primary_auth()
    return jsonify(**result), status


@app.route("/api/action/webauthn/options", methods=["POST"])
def api_action_webauthn_options():
    guard = _require_login()
    if guard:
        return guard
    if not _pending_action():
        return jsonify(success=False, message="لا يوجد طلب معلّق"), 404

    account = load_account()
    cred = account.get("webauthn")
    if not cred:
        return jsonify(success=False, message="الدخول بالبصمة غير مفعّل على هذا الحساب"), 404

    challenge = os.urandom(32)
    _set_webauthn_challenge("action_webauthn_challenge", _b64url_encode(challenge), _rp_id())

    options = {
        "challenge": _b64url_encode(challenge),
        "rpId": _rp_id(),
        "allowCredentials": [{"id": cred["id"], "type": "public-key", "transports": ["internal"]}],
        "userVerification": "required",
        "timeout": 60000,
    }
    return jsonify(options)


@app.route("/api/action/webauthn/verify", methods=["POST"])
def api_action_webauthn_verify():
    guard = _require_login()
    if guard:
        return guard
    if not _pending_action():
        return jsonify(success=False, message="لا يوجد طلب معلّق أو انتهت صلاحيته"), 404

    data = request.json or {}
    account = load_account()
    cred = account.get("webauthn")
    expected_challenge, _rp_id_at_challenge_time = _pop_webauthn_challenge("action_webauthn_challenge")

    if not expected_challenge:
        return jsonify(success=False, message="انتهت صلاحية الطلب، حاول مرة أخرى"), 400

    enrolled_rp_id = cred.get("rp_id") if cred else None
    if enrolled_rp_id and enrolled_rp_id != _rp_id():
        return jsonify(
            success=False,
            message=f"تم تفعيل البصمة على عنوان مختلف ({enrolled_rp_id}). افتح التطبيق من نفس العنوان أو أعد تفعيل البصمة من الإعدادات.",
        ), 400

    ok, err = _verify_webauthn_assertion(data, expected_challenge, cred)
    if not ok:
        return jsonify(success=False, message=err), 400

    result, status = _complete_primary_auth()
    return jsonify(**result), status


# -----------------------------------------------------------------------
# Account / cards / transactions / notifications API
# -----------------------------------------------------------------------
@app.route("/api/account")
def api_account():
    account = load_account()
    account.pop("password", None)
    return jsonify(account)


@app.route("/api/notifications/read", methods=["POST"])
def api_notifications_read():
    session["notifications_read"] = True
    return jsonify(success=True)


@app.route("/api/settings/update", methods=["POST"])
def api_settings_update():
    data = request.json or {}
    account = load_account()

    if data.get("name"):
        account["name"] = data["name"]
    if data.get("phone"):
        account["phone"] = data["phone"]
    if data.get("new_password"):
        account["password"] = data["new_password"]

    save_account(account)
    return jsonify(success=True, message="تم حفظ الإعدادات بنجاح", account=account)


# -----------------------------------------------------------------------
# Voice subsystem API — Settings -> Voice preferences, and TTS synthesis.
# Fully optional/degradeable: if `voice_tts` is None (package/providers
# unavailable), /api/voice/settings still works (it's just data on
# account.json), and /api/tts always reports the browser fallback so the
# client transparently keeps using its existing Web Speech API voice.
# -----------------------------------------------------------------------
@app.route("/api/voice/settings", methods=["GET"])
def api_voice_settings_get():
    guard = _require_login()
    if guard:
        return guard
    account = load_account()
    if voice_tts is not None:
        return jsonify(success=True, settings=voice_tts.get_settings(account))
    from voice import settings as voice_settings_mod
    return jsonify(success=True, settings=voice_settings_mod.get_voice_settings(account))


@app.route("/api/voice/settings", methods=["POST"])
def api_voice_settings_update():
    guard = _require_login()
    if guard:
        return guard
    data = request.json or {}
    account = load_account()

    if voice_tts is not None:
        new_settings, error = voice_tts.update_settings(account, data)
    else:
        from voice import settings as voice_settings_mod
        new_settings, error = voice_settings_mod.validate_and_merge(account, data)

    if error:
        return jsonify(success=False, message=error), 400

    account["voice_settings"] = new_settings
    save_account(account)
    return jsonify(success=True, message="تم حفظ إعدادات الصوت", settings=new_settings)


# -----------------------------------------------------------------------
# Accessibility API — Settings -> Accessibility (Senior Mode, high
# contrast, automatic screen reading, and the granular read_* toggles).
# Speech rate / preferred voice are intentionally NOT duplicated here --
# the Accessibility page reads/writes the existing /api/voice/settings
# directly, same single source of truth used by Settings -> Voice.
# -----------------------------------------------------------------------
@app.route("/api/accessibility/settings", methods=["GET"])
def api_accessibility_settings_get():
    guard = _require_login()
    if guard:
        return guard
    account = load_account()
    if accessibility_engine is not None:
        return jsonify(success=True, settings=accessibility_engine.get_settings(account))
    from accessibility import settings as accessibility_settings_mod
    return jsonify(success=True, settings=accessibility_settings_mod.get_settings(account))


@app.route("/api/accessibility/settings", methods=["POST"])
def api_accessibility_settings_update():
    guard = _require_login()
    if guard:
        return guard
    data = request.json or {}
    account = load_account()

    if accessibility_engine is not None:
        new_settings, error = accessibility_engine.update_settings(account, data)
    else:
        from accessibility import settings as accessibility_settings_mod
        new_settings, error = accessibility_settings_mod.validate_and_merge(account, data)
        if not error:
            account["accessibility"] = new_settings

    if error:
        return jsonify(success=False, message=error), 400

    save_account(account)
    return jsonify(success=True, message="تم حفظ إعدادات إمكانية الوصول", settings=new_settings)


@app.route("/accessibility-page")
def accessibility_page():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("accessibility.html", account=load_account())


@app.route("/api/tts", methods=["POST"])
def api_tts():
    guard = _require_login()
    if guard:
        return guard
    if voice_tts is None:
        return jsonify(success=False, fallback="browser")

    data = request.json or {}
    text = (data.get("text") or "").strip()
    lang = data.get("lang")
    if not text:
        return jsonify(success=False, message="لا يوجد نص لتحويله إلى صوت"), 400

    account = load_account()
    result = voice_tts.synthesize(text, account, lang=lang)
    if result is None:
        return jsonify(success=False, fallback="browser")

    audio_bytes, mime_type = result
    return app.response_class(audio_bytes, mimetype=mime_type)


@app.route("/api/tts/stream", methods=["POST"])
def api_tts_stream():
    """Streaming variant: chunked response so the client can start playing
    before the full clip finishes generating. Falls back to the same
    {"fallback": "browser"} JSON signal if no provider can even start —
    checked eagerly (before starting to stream) so the client never gets a
    half-started, ambiguous response."""
    guard = _require_login()
    if guard:
        return guard
    if voice_tts is None:
        return jsonify(success=False, fallback="browser")

    data = request.json or {}
    text = (data.get("text") or "").strip()
    lang = data.get("lang")
    if not text:
        return jsonify(success=False, message="لا يوجد نص لتحويله إلى صوت"), 400

    account = load_account()

    from voice.providers import TTSUnavailable

    def generate():
        try:
            for chunk in voice_tts.stream_synthesize(text, account, lang=lang):
                yield chunk
        except TTSUnavailable:
            return  # client will notice the empty/short body and fall back

    # Eagerly probe availability so we don't start a 200 stream that then
    # immediately produces zero bytes with no way to signal "use browser".
    if not voice_tts.can_synthesize(account):
        return jsonify(success=False, fallback="browser")

    return app.response_class(generate(), mimetype=voice_tts.MIME_TYPE)


# -----------------------------------------------------------------------
# Voice Authentication API — Settings -> Security -> Voice Authentication,
# and the optional additional verification step inserted between primary
# auth (OTP/WebAuthn) and execution. See _complete_primary_auth() above
# for how this plugs into the existing security gate without altering it
# for any account that hasn't enabled this feature.
# -----------------------------------------------------------------------
@app.route("/api/voice-auth/status")
def api_voice_auth_status():
    guard = _require_login()
    if guard:
        return guard
    account = load_account()
    if voice_auth_engine is None:
        return jsonify(success=True, status={
            "available": False, "enrolled": False, "enabled": False,
            "provider": None, "sample_count": 0, "threshold": 0.75, "enrolled_at": None,
        })
    return jsonify(success=True, status=voice_auth_engine.get_status(account))


@app.route("/api/voice-auth/enroll", methods=["POST"])
def api_voice_auth_enroll():
    guard = _require_login()
    if guard:
        return guard
    if voice_auth_engine is None:
        return jsonify(success=False, message="ميزة التحقق الصوتي غير متاحة على هذا الخادم حالياً"), 503

    data = request.json or {}
    samples_b64 = data.get("samples") or []
    if not isinstance(samples_b64, list):
        return jsonify(success=False, message="صيغة العينات الصوتية غير صحيحة"), 400

    try:
        samples = [base64.b64decode(s) for s in samples_b64]
    except Exception:
        return jsonify(success=False, message="تعذر فك ترميز العينات الصوتية"), 400

    account = load_account()
    ok, error = voice_auth_engine.enroll(account, samples)
    if not ok:
        return jsonify(success=False, message=error), 400

    save_account(account)
    return jsonify(success=True, message="تم تسجيل بصمتك الصوتية بنجاح", status=voice_auth_engine.get_status(account))


@app.route("/api/voice-auth/delete", methods=["POST"])
def api_voice_auth_delete():
    guard = _require_login()
    if guard:
        return guard
    account = load_account()
    if voice_auth_engine is not None:
        voice_auth_engine.delete(account)
    else:
        account.pop("voice_auth", None)
    save_account(account)
    return jsonify(success=True, message="تم حذف بصمتك الصوتية")


@app.route("/api/voice-auth/settings", methods=["POST"])
def api_voice_auth_settings_update():
    guard = _require_login()
    if guard:
        return guard
    data = request.json or {}
    account = load_account()

    if voice_auth_engine is not None:
        ok, error = voice_auth_engine.update_settings(account, data)
    else:
        from voice_auth import settings as voice_auth_settings_mod
        ok, error = voice_auth_settings_mod.apply_settings_update(account, data)

    if not ok:
        return jsonify(success=False, message=error), 400

    save_account(account)
    status = voice_auth_engine.get_status(account) if voice_auth_engine is not None else {}
    return jsonify(success=True, message="تم حفظ إعدادات التحقق الصوتي", status=status)


@app.route("/api/action/voice/verify", methods=["POST"])
def api_action_voice_verify():
    """The ONLY route that can execute a pending action for a voice-auth-
    enabled account. Hard-requires primary_verified=True on the CURRENT
    pending action (set exclusively by _complete_primary_auth() after a
    real OTP/WebAuthn success) — so this can never be used to bypass
    primary authentication, only to add a step after it."""
    guard = _require_login()
    if guard:
        return guard

    pending = _pending_action()
    if not pending:
        return jsonify(success=False, message="لا يوجد طلب معلّق أو انتهت صلاحيته"), 404
    if not pending.get("primary_verified"):
        return jsonify(success=False, message="يجب إكمال التحقق الأساسي (البصمة أو رمز التحقق) أولاً"), 400

    account = load_account()
    if voice_auth_engine is None or not voice_auth_engine.is_enabled(account):
        return jsonify(success=False, message="التحقق الصوتي غير مفعّل على هذا الحساب"), 400

    data = request.json or {}
    audio_b64 = data.get("audio")
    if not audio_b64:
        return jsonify(success=False, message="لم يتم إرسال أي تسجيل صوتي"), 400
    try:
        audio_bytes = base64.b64decode(audio_b64)
    except Exception:
        return jsonify(success=False, message="بيانات صوتية غير صالحة"), 400

    result = voice_auth_engine.verify_for_pending_action(account, audio_bytes)
    if not result["accepted"]:
        return jsonify(success=False, message=result["message"], confidence=result["confidence"]), 400

    exec_result = _execute_pending_action()
    status = 200 if exec_result.get("success") else 400
    return jsonify(**exec_result), status


# -----------------------------------------------------------------------
# Beneficiaries API (read + delete only — creation always goes through the
# authentication gate above, see /api/action/create)
# -----------------------------------------------------------------------
@app.route("/beneficiaries")
def api_beneficiaries():
    return jsonify(load_beneficiaries())


@app.route("/api/beneficiaries/delete", methods=["POST"])
def api_delete_beneficiary():
    d = request.json or {}
    name = (d.get("name") or "").strip()

    beneficiary.load()
    removed = beneficiary.remove(name)

    if not removed:
        return jsonify(success=False, message="المستفيد غير موجود"), 404

    return jsonify(success=True, message="تم حذف المستفيد", beneficiaries=beneficiary.get_all())


# -----------------------------------------------------------------------
# Assistant API — bilingual, screen-aware, voice-driven transfers and
# beneficiary creation, voice navigation, all gated by /api/action/*.
# -----------------------------------------------------------------------
INTENT_REPLIES = {
    "balance": lambda a: f"رصيدك الحالي هو {a['balance']:g} ريال.",
    "cards": lambda a: "هذه بطاقاتك:\n" + "\n".join(
        f"• {c['type']} المنتهية بـ {c['number']}" for c in a["cards"]
    ),
    "transactions": lambda a: "آخر العمليات:\n" + "\n".join(f"• {t}" for t in a["transactions"][:5]),
    "notifications": lambda a: (
        "\n".join(f"• {n}" for n in a["notifications"][:5]) if a["notifications"] else "لا توجد إشعارات جديدة."
    ),
    "home": lambda a: "تم الانتقال إلى الصفحة الرئيسية.",
    "settings": lambda a: "تم فتح صفحة الإعدادات.",
    "branch": lambda a: "تم العثور على أقرب فرع لمصرف الإنماء.",
    "atm": lambda a: "تم العثور على أقرب جهاز صراف آلي.",
    "beneficiary": lambda a: "تم فتح صفحة المستفيدين.",
    "accounts": lambda a: f"اسم صاحب الحساب: {a['name']}.",
    "help": lambda a: "يمكنني مساعدتك في الرصيد والبطاقات وكشف الحساب والتحويلات وإضافة مستفيدين والتنقل بالصوت.",
}

INTENT_ACTIONS = {
    "cards": "/cards",
    "transactions": "/transactions-page",
    "notifications": "/notifications-page",
    "home": "/dashboard",
    "settings": "/settings",
    "beneficiary": "/beneficiaries-page",
}

NAV_TARGETS = {
    "dashboard": ("/dashboard", "الصفحة الرئيسية"),
    "cards": ("/cards", "البطاقات"),
    "transfer": ("/transfer", "صفحة التحويل"),
    "beneficiaries-page": ("/beneficiaries-page", "صفحة المستفيدين"),
    "notifications-page": ("/notifications-page", "الإشعارات"),
    "transactions-page": ("/transactions-page", "كشف الحساب"),
    "settings": ("/settings", "الإعدادات"),
    "assistant": ("/assistant", "المساعد الصوتي"),
}


# Below this, the local classifier's guess is not trusted on its own — the
# AI fallback gets a chance first (see detect_intent_with_confidence()).
INTENT_CONFIDENCE_THRESHOLD = 0.55


def _fmt_amount(n):
    try:
        return f"{float(n):g}"
    except (TypeError, ValueError):
        return str(n)


def _clear_assistant_state():
    session["assistant_state"] = None


def _ask_method_or_gate(flow, payload, action_type):
    """Shared by both the transfer and add-beneficiary flows: once we have
    everything we need, either ask which auth method to use (if biometrics
    are enrolled) or go straight to the auth-verify page (OTP only)."""
    account = load_account()
    if account.get("webauthn"):
        session["assistant_state"] = {"flow": flow, "stage": "choose_method", "payload": payload, "action_type": action_type}
        return {
            "response": "هل تريد التحقق بالبصمة أم برمز التحقق عبر الرسائل؟",
            "action": None,
        }

    ok, message, _token = _create_pending_action(action_type, payload)
    _clear_assistant_state()
    if not ok:
        return {"response": message, "action": None}
    return {
        "response": "سأفتح شاشة التحقق برمز الرسائل الآن لإتمام العملية.",
        "action": {"navigate": "/auth-verify?method=otp"},
    }


def _continue_choose_method(message, state):
    method = nlu.detect_auth_method(message)
    payload = state["payload"]
    action_type = state["action_type"]

    if method is None:
        return {"response": "لم أفهم، هل تريد التحقق بالبصمة أم برمز التحقق؟", "action": None}

    ok, msg, _token = _create_pending_action(action_type, payload)
    _clear_assistant_state()
    if not ok:
        return {"response": msg, "action": None}

    if method == "webauthn":
        return {
            "response": "سأفتح شاشة التحقق بالبصمة الآن.",
            "action": {"navigate": "/auth-verify?method=webauthn"},
        }
    return {
        "response": "سأفتح شاشة التحقق برمز الرسائل الآن.",
        "action": {"navigate": "/auth-verify?method=otp"},
    }


# ---- Transfer flow ----
def _begin_transfer_with_slots(amount, name_hint, beneficiaries):
    """Shared core used by BOTH the local-NLU path and the AI-fallback path.
    Takes already-extracted (amount, name_hint) — never re-parses text —
    resolves the beneficiary against the real beneficiaries list (the AI's
    guess is never trusted directly), and sets the same session state the
    rest of the multi-turn flow already understands."""
    ben = nlu.find_beneficiary(beneficiaries, name_hint) if name_hint else None

    if ben and amount:
        session["assistant_state"] = {
            "flow": "transfer", "stage": "confirm",
            "amount": amount, "beneficiary": ben["name"],
        }
        return {
            "response": f"هل تريد تحويل {_fmt_amount(amount)} ريال إلى {ben['name']}؟ قل نعم للتأكيد أو لا للإلغاء.",
            "action": None,
        }
    if ben and not amount:
        session["assistant_state"] = {"flow": "transfer", "stage": "await_amount", "beneficiary": ben["name"]}
        return {"response": f"كم المبلغ الذي تريد تحويله إلى {ben['name']}؟", "action": None}
    if amount and not ben:
        session["assistant_state"] = {"flow": "transfer", "stage": "await_beneficiary", "amount": amount}
        extra = f" لم أجد مستفيداً باسم {name_hint}." if name_hint else ""
        return {"response": f"لمن تريد تحويل {_fmt_amount(amount)} ريال؟{extra}", "action": None}

    session["assistant_state"] = {"flow": "transfer", "stage": "await_beneficiary_and_amount"}
    return {"response": "بكل سرور، من تريد أن تحول له وكم المبلغ؟", "action": None}


def _start_transfer_flow(message, context, beneficiaries):
    amount, name_hint = nlu.extract_transfer(message)
    amount = amount or context.get("entered_amount")
    name_hint = name_hint or context.get("selected_beneficiary")
    return _begin_transfer_with_slots(amount, name_hint, beneficiaries)


def _continue_transfer_flow(message, state):
    beneficiaries = load_beneficiaries()
    stage = state.get("stage")

    if stage == "await_beneficiary_and_amount":
        amount, name_hint = nlu.extract_transfer(message)
        # A bare follow-up reply ("أحمد" / "خمسين") has no trigger verb, so
        # extract_transfer() alone won't catch it — try both independently.
        if not amount:
            amount = nlu.parse_amount(message)
        ben = nlu.find_beneficiary(beneficiaries, name_hint or message)
        if ben:
            state["beneficiary"] = ben["name"]
        if amount:
            state["amount"] = amount
        if state.get("beneficiary") and state.get("amount"):
            state["stage"] = "confirm"
            session["assistant_state"] = state
            return {
                "response": f"هل تريد تحويل {_fmt_amount(state['amount'])} ريال إلى {state['beneficiary']}؟ قل نعم للتأكيد.",
                "action": None,
            }
        session["assistant_state"] = state
        missing = "اسم المستفيد" if not state.get("beneficiary") else "المبلغ"
        return {"response": f"لم أفهم بعد، من فضلك أخبرني {missing}.", "action": None}

    if stage == "await_amount":
        amount = nlu.parse_amount(message)
        if not amount:
            return {"response": "لم أفهم المبلغ، كم ريال تريد أن تحول؟", "action": None}
        state["amount"] = amount
        state["stage"] = "confirm"
        session["assistant_state"] = state
        return {
            "response": f"هل تريد تحويل {_fmt_amount(amount)} ريال إلى {state['beneficiary']}؟ قل نعم للتأكيد.",
            "action": None,
        }

    if stage == "await_beneficiary":
        _, name_hint = nlu.extract_transfer(message)
        name_hint = name_hint or message
        ben = nlu.find_beneficiary(beneficiaries, name_hint)
        if not ben:
            return {"response": f"لم أجد مستفيداً باسم {name_hint}. حاول مرة أخرى أو أضفه بقول \"أضف مستفيد\".", "action": None}
        state["beneficiary"] = ben["name"]
        state["stage"] = "confirm"
        session["assistant_state"] = state
        return {
            "response": f"هل تريد تحويل {_fmt_amount(state['amount'])} ريال إلى {ben['name']}؟ قل نعم للتأكيد.",
            "action": None,
        }

    if stage == "confirm":
        if nlu.is_affirmative(message):
            return _ask_method_or_gate("transfer", {"beneficiary": state["beneficiary"], "amount": state["amount"]}, "transfer")
        return {"response": "لم أفهم، هل تؤكد التحويل؟ قل نعم أو لا.", "action": None}

    if stage == "choose_method":
        return _continue_choose_method(message, state)

    _clear_assistant_state()
    return None


# ---- Add-beneficiary flow ----
def _start_add_beneficiary_flow():
    session["assistant_state"] = {"flow": "add_beneficiary", "stage": "await_name"}
    return {"response": "بكل سرور، ما اسم المستفيد الجديد؟", "action": None}


def _continue_add_beneficiary_flow(message, state):
    stage = state.get("stage")

    if stage == "await_name":
        name = message.strip()
        if not name:
            return {"response": "لم أفهم الاسم، من فضلك أعد المحاولة.", "action": None}
        state["name"] = name
        state["stage"] = "await_iban"
        session["assistant_state"] = state
        return {"response": f"تمام، ما رقم الآيبان (IBAN) الخاص بـ {name}؟", "action": None}

    if stage == "await_iban":
        iban = nlu._normalize_digits(message.strip()).upper().replace(" ", "")
        if len(iban) < 8:
            return {"response": "رقم الآيبان يبدو قصيراً جداً، من فضلك أعد قوله أو اكتبه.", "action": None}
        state["iban"] = iban
        state["stage"] = "await_nickname"
        session["assistant_state"] = state
        return {"response": "وما الاسم المختصر؟ يمكنك قول \"تخطي\" لاستخدام نفس الاسم.", "action": None}

    if stage == "await_nickname":
        text = message.strip()
        nickname = state["name"] if (nlu.is_negative(text) or "تخط" in text or "skip" in text.lower()) else text
        payload = {"name": state["name"], "iban": state["iban"], "nickname": nickname}
        return _ask_method_or_gate("add_beneficiary", payload, "add_beneficiary")

    if stage == "choose_method":
        return _continue_choose_method(message, state)

    _clear_assistant_state()
    return None


# ---- Voice navigation ----
def _handle_navigation(nav):
    if nav == "read_screen":
        return {"response": "سأقرأ محتوى الشاشة الآن.", "action": {"readScreen": True}}
    if nav == "back":
        return {"response": "جارِ الرجوع للصفحة السابقة.", "action": {"goBack": True}}
    if nav == "close":
        return {"response": "تم إغلاق المساعد.", "action": {"close": True}}
    if nav == "cancel":
        _clear_assistant_state()
        session.pop("pending_action", None)
        return {"response": "تم إلغاء العملية الحالية.", "action": None}
    if nav == "logout":
        session.clear()
        return {"response": "تم تسجيل الخروج.", "action": {"navigate": "/"}}
    if nav in ("next", "previous"):
        return {"response": "لا يوجد المزيد لعرضه هنا حالياً.", "action": None}
    if nav in NAV_TARGETS:
        path, label = NAV_TARGETS[nav]
        return {"response": f"جارِ فتح {label}...", "action": {"navigate": path}}
    return None


# -----------------------------------------------------------------------
# AI fallback dispatch — only reached when the fast local layer (NLU +
# state machine above) could not confidently handle the message. Returns
# either a dict ready to jsonify(), or None to mean "give up, use the
# existing default reply" (e.g. AI unavailable, or it proposed something
# that couldn't be resolved safely).
#
# Security invariant: a "propose_transfer"/"propose_add_beneficiary" tool
# call is routed through _ask_method_or_gate() — the EXACT SAME function
# the local NLU flow uses — so it can only ever reach the same
# pending_action -> OTP/WebAuthn -> _execute_pending_action() gate. The AI
# has no other path to move money or add a beneficiary.
# -----------------------------------------------------------------------
def _try_ai_fallback(message, context):
    if ai_engine is None:
        print("NOTE: _try_ai_fallback() called but ai_engine is None (import failed at startup — see the NOTE printed above) — falling back to local NLU.", file=sys.stderr)
        return None

    account = load_account()
    current_page = (context or {}).get("page") or "unknown"

    senior_mode = False
    if accessibility_engine is not None:
        try:
            senior_mode = bool(accessibility_engine.get_settings(account).get("senior_mode"))
        except Exception:
            senior_mode = False

    try:
        decision = ai_engine.get_ai_reply(message, session, account, current_page, senior_mode=senior_mode)
    except Exception:
        # The AI layer is defensive internally too, but this is a hard
        # backstop: it must never be able to break the assistant.
        return None

    if decision.get("unavailable"):
        return None

    if decision.get("type") == "text":
        return {"response": decision.get("text") or "", "action": None}

    tool = decision.get("tool")
    args = decision.get("args") or {}

    if tool == "propose_transfer":
        beneficiaries = load_beneficiaries()
        ben = nlu.find_beneficiary(beneficiaries, str(args.get("beneficiary_name") or ""))
        try:
            amount = float(args.get("amount"))
        except (TypeError, ValueError):
            amount = None
        if ben and amount and amount > 0:
            return _ask_method_or_gate("transfer", {"beneficiary": ben["name"], "amount": amount}, "transfer")
        missing = "اسم المستفيد" if not ben else "المبلغ"
        return {"response": f"لم أستطع تأكيد {missing} بدقة، من فضلك وضّح أكثر.", "action": None}

    if tool == "propose_add_beneficiary":
        name = str(args.get("name") or "").strip()
        iban = str(args.get("iban") or "").strip()
        nickname = str(args.get("nickname") or "").strip() or name
        if name and iban:
            return _ask_method_or_gate(
                "add_beneficiary", {"name": name, "iban": iban, "nickname": nickname}, "add_beneficiary"
            )
        return {"response": "أحتاج اسم المستفيد ورقم الآيبان لإضافته.", "action": None}

    if tool == "navigate":
        result = _handle_navigation(args.get("target"))
        if result is not None:
            return result
        return None

    return None


def _resolve_locally(message, context):
    """Every fast, LOCAL (non-AI) resolution step of the assistant pipeline:
    pre-login gating, cancel/confirm handling, active multi-turn flow
    continuation, flow starts, voice navigation, and confident single-turn
    intents. Shared by both /api/assistant (synchronous) and
    /api/assistant/stream (SSE), so the two endpoints can NEVER diverge on
    anything security-sensitive (OTP/WebAuthn-gated transfer/add-beneficiary
    flows, navigation, cancel/confirm) — only the final "ask OpenAI a plain
    question" step differs (blocking vs. streamed) between them.

    Returns (result_dict_or_None, intent, intent_confidence, needs_ai):
      - result_dict_or_None: a ready-to-return {"response", "action"} dict
        if something local resolved the message, else None.
      - needs_ai: True only when nothing local resolved it AND the caller
        should hand the message to the AI fallback layer next.
    """
    if not message:
        return {"response": "لم أسمعك جيداً، هل يمكنك إعادة المحاولة؟", "action": None}, "unknown", 0.0, False

    # HIGHEST PRIORITY, ALWAYS FIRST: a safety emergency (threat, fraud,
    # theft, a hacked/compromised account) bypasses login gating, any
    # active flow, local-intent detection, and the AI fallback entirely.
    # No clarifying questions — immediate safety response + navigation.
    # Any in-flight transfer/add-beneficiary flow or pending action is
    # dropped, since the account may be compromised.
    if nlu.is_emergency(message):
        account_for_lockout = load_account()
        _trigger_emergency_lockout(account_for_lockout.get("phone") or "default")
        session.clear()  # full, immediate logout — not just clearing the assistant flow
        return {
            "response": (
                "تم اكتشاف حالة طوارئ. تم إرسال بلاغ أمني لحماية الحساب. "
                f"سيتم إعادة تفعيل تسجيل الدخول خلال {EMERGENCY_LOCKOUT_SECONDS} ثانية."
            ),
            "action": {"navigate": "/", "emergencyLogout": True},
        }, "emergency", 1.0, False

    # Pre-login (e.g. on the login screen itself): the assistant can still
    # help with navigation/reading the screen, but must never answer
    # account-specific questions before authentication.
    if not is_logged_in():
        nav = nlu.detect_navigation(message)
        if nav == "read_screen":
            return {"response": "سأقرأ محتوى الشاشة الآن.", "action": {"readScreen": True}}, "unknown", 0.0, False
        if nav in ("cancel", "close"):
            return {"response": "تم.", "action": None}, "unknown", 0.0, False
        return {
            "response": "الرجاء تسجيل الدخول أولاً حتى أتمكن من مساعدتك في حسابك. يمكنك قول \"اقرأ الشاشة\" للمساعدة في تسجيل الدخول.",
            "action": None,
        }, "unknown", 0.0, False

    account = load_account()
    state = session.get("assistant_state")

    # Cancel always breaks out of any active flow first.
    if nlu.detect_navigation(message) == "cancel" and (state or session.get("pending_action")):
        return _handle_navigation("cancel"), "unknown", 0.0, False

    # A bare "no" while specifically at a yes/no confirmation step cancels
    # that flow. (Other stages, like the beneficiary nickname step, use "لا"
    # to mean something else — "skip" — so this only applies to "confirm".)
    if state and state.get("stage") == "confirm" and nlu.is_negative(message):
        _clear_assistant_state()
        return {"response": "تم إلغاء العملية.", "action": None}, "unknown", 0.0, False

    # A clear navigation command can interrupt a flow that's only waiting on
    # an enumerable answer (yes/no, fingerprint/otp) — but NOT a free-text
    # data-entry stage (name/IBAN/amount), where those same words could be
    # legitimate input.
    if state and state.get("stage") in ("confirm", "choose_method"):
        nav_override = nlu.detect_navigation(message)
        if nav_override and nav_override != "cancel":
            _clear_assistant_state()
            result = _handle_navigation(nav_override)
            if result is not None:
                return result, "unknown", 0.0, False

    # Continue an active conversation (transfer or add-beneficiary).
    if state:
        flow = state.get("flow")
        if flow == "transfer":
            result = _continue_transfer_flow(message, state)
        elif flow == "add_beneficiary":
            result = _continue_add_beneficiary_flow(message, state)
        else:
            result = None
        if result is not None:
            return result, "unknown", 0.0, False

    # Start a new add-beneficiary conversation.
    if nlu.looks_like_add_beneficiary(message):
        return _start_add_beneficiary_flow(), "unknown", 0.0, False

    # Start a new transfer conversation.
    intent, intent_confidence = nlu.detect_intent_with_confidence(message)
    if intent == "transfer" or nlu.looks_like_transfer(message):
        # The trigger regex only detects *intent to transfer*, not whether
        # we can actually extract an amount/name from it (e.g. dialectal
        # number words like "ميتين" aren't in our dictionary, or a made-up
        # name doesn't parse as one). If extraction found nothing at all,
        # give the AI a chance before falling back to the local flow's own
        # generic "who and how much?" follow-up. This AI call stays
        # synchronous/non-streamed on purpose: it may resolve straight into
        # a security-gated transfer proposal, which must never be handled
        # as a streamed chat reply.
        amount_probe, name_probe = nlu.extract_transfer(message)
        has_context_hint = bool(context.get("entered_amount") or context.get("selected_beneficiary"))
        if not (amount_probe or name_probe or has_context_hint):
            ai_result = _try_ai_fallback(message, context)
            if ai_result is not None:
                return ai_result, intent, intent_confidence, False

        result = _start_transfer_flow(message, context, load_beneficiaries())
        if result is not None:
            return result, intent, intent_confidence, False

    # Voice navigation commands.
    nav = nlu.detect_navigation(message)
    if nav:
        result = _handle_navigation(nav)
        if result is not None:
            return result, intent, intent_confidence, False

    # Regular single-turn intents — fast local path, but only trusted when
    # the classifier is actually confident. A forced-choice classifier over
    # ~12 intents always predicts *something*, so "intent != unknown" alone
    # isn't a reliable signal; see detect_intent_with_confidence().
    reply_fn = INTENT_REPLIES.get(intent)
    if reply_fn and intent_confidence >= INTENT_CONFIDENCE_THRESHOLD:
        response_text = reply_fn(account)
        action = {"navigate": INTENT_ACTIONS[intent]} if intent in INTENT_ACTIONS else None
        return {"response": response_text, "action": action}, intent, intent_confidence, False

    # Nothing local resolved this with confidence — hand it to the AI layer.
    return None, intent, intent_confidence, True


@app.route("/api/assistant", methods=["POST"])
def api_assistant():
    data = request.json or {}
    message = (data.get("message") or "").strip()
    context = data.get("context") or {}

    local_result, intent, intent_confidence, needs_ai = _resolve_locally(message, context)
    if local_result is not None:
        return jsonify(**local_result, intent=intent)

    if not needs_ai:
        response_text = "عذراً، لم أفهم طلبك. يمكنك سؤالي عن الرصيد أو البطاقات أو التحويل أو إضافة مستفيد أو الانتقال بين الصفحات."
        return jsonify(response=response_text, action=None, intent=intent)

    # Local layer is unconfident (or has no handler at all). Try the AI
    # fallback before ever returning a generic/possibly-wrong response.
    ai_result = _try_ai_fallback(message, context)
    if ai_result is not None:
        return jsonify(**ai_result, intent=intent)

    # AI unavailable AND the local classifier wasn't actually confident:
    # never guess a navigation/action from a low-confidence local intent —
    # that's exactly how something like "أنا مسافر" (a travel remark, not a
    # command) used to get force-mapped to an unrelated page.
    response_text = "عذراً، لم أفهم طلبك. يمكنك سؤالي عن الرصيد أو البطاقات أو التحويل أو إضافة مستفيد أو الانتقال بين الصفحات."
    return jsonify(response=response_text, action=None, intent=intent)


@app.route("/api/assistant/stream", methods=["POST"])
def api_assistant_stream():
    """Server-Sent Events variant of /api/assistant. Every local/security-
    sensitive resolution path is IDENTICAL to /api/assistant (both call
    _resolve_locally) and is sent back as a single non-streamed 'final'
    event — only a genuine open-ended AI text answer (e.g. "كيف أحصل على
    قرض؟") is actually streamed token-by-token. If the AI decides to
    propose a tool call (transfer/add-beneficiary/navigate) mid-stream, or
    fails outright, this emits {"type": "redo_sync"} and the frontend
    re-sends the same message to the plain /api/assistant endpoint, which
    routes tool proposals through the exact same OTP/WebAuthn gate as
    always — streaming NEVER bypasses that gate."""
    data = request.json or {}
    message = (data.get("message") or "").strip()
    context = data.get("context") or {}

    def sse(obj):
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    local_result, intent, intent_confidence, needs_ai = _resolve_locally(message, context)

    if local_result is not None or not needs_ai:
        payload = local_result or {
            "response": "عذراً، لم أفهم طلبك. يمكنك سؤالي عن الرصيد أو البطاقات أو التحويل أو إضافة مستفيد أو الانتقال بين الصفحات.",
            "action": None,
        }

        def gen_local():
            yield sse({"type": "final", **payload, "intent": intent})

        return app.response_class(gen_local(), mimetype="text/event-stream")

    if ai_engine is None:
        def gen_unavailable():
            response_text = "عذراً، لم أفهم طلبك. يمكنك سؤالي عن الرصيد أو البطاقات أو التحويل أو إضافة مستفيد أو الانتقال بين الصفحات."
            yield sse({"type": "final", "response": response_text, "action": None, "intent": intent})

        return app.response_class(gen_unavailable(), mimetype="text/event-stream")

    account = load_account()
    current_page = (context or {}).get("page") or "unknown"
    senior_mode = False
    if accessibility_engine is not None:
        try:
            senior_mode = bool(accessibility_engine.get_settings(account).get("senior_mode"))
        except Exception:
            senior_mode = False

    def gen_ai():
        try:
            for event in ai_engine.stream_ai_reply(message, session, account, current_page, senior_mode=senior_mode):
                kind = event.get("kind")
                if kind == "delta":
                    yield sse({"type": "delta", "text": event.get("text") or ""})
                elif kind == "done_text":
                    yield sse({"type": "final", "response": event.get("text") or "", "action": None, "intent": intent})
                    return
                elif kind in ("tool", "unavailable"):
                    # A tool proposal or an outright AI failure: both must
                    # go through the exact same synchronous, fully-gated
                    # logic as /api/assistant rather than being duplicated
                    # here, so hand it back to the client to redo as a
                    # normal (non-streaming) request.
                    yield sse({"type": "redo_sync"})
                    return
            yield sse({"type": "redo_sync"})
        except Exception:
            yield sse({"type": "redo_sync"})

    return app.response_class(gen_ai(), mimetype="text/event-stream")


# -----------------------------------------------------------------------
# Health check — for production monitoring/load balancers. Deliberately
# reports subsystem availability (not just "process is alive") since a
# silently-degraded optional subsystem (AI, TTS, voice auth) is exactly
# the kind of thing that should show up in monitoring, even though the
# app itself keeps working via its graceful-fallback design.
# -----------------------------------------------------------------------
@app.route("/health")
def health():
    ai_module_loaded = ai_engine is not None
    ai_package_installed = False
    ai_key_present = bool(os.environ.get("OPENAI_API_KEY"))
    ai_available = False
    if ai_module_loaded:
        # Best-effort config check (not a live network probe): the openai
        # package must be importable AND an API key must be configured.
        try:
            import openai  # noqa: F401
            ai_package_installed = True
            ai_available = ai_key_present
        except ImportError:
            ai_package_installed = False
            ai_available = False

    voice_tts_available = False
    if voice_tts is not None:
        try:
            real_providers = {k: v for k, v in voice_tts._provider_instances.items() if k != "browser"}
            voice_tts_available = any(p.is_available() for p in real_providers.values())
        except Exception:
            voice_tts_available = False

    voice_auth_available = bool(voice_auth_engine is not None and voice_auth_engine.is_available())

    return jsonify(
        status="ok",
        subsystems={
            "ai_fallback": ai_available,
            "voice_tts": voice_tts_available,
            "voice_auth": voice_auth_available,
            "accessibility": accessibility_engine is not None,
        },
        ai_diagnostics={
            "module_loaded": ai_module_loaded,
            "openai_package_installed": ai_package_installed,
            "openai_api_key_present": ai_key_present,
            "note": (
                "ai_fallback is only true when ALL THREE of module_loaded, "
                "openai_package_installed, and openai_api_key_present are true. "
                "If module_loaded is false, check the startup log for the NOTE "
                "printed when `from ai import ai_engine` failed. Run the server "
                "with SANAD_AI_DEBUG=1 for verbose per-request OpenAI call logging."
            ),
        },
    )


@app.route("/api/stt", methods=["POST"])
def api_stt():
    """Server-side speech-to-text fallback (Whisper), used by the frontend
    ONLY when native browser SpeechRecognition is unavailable or fails to
    actually work (see static/js/stt_fallback.js + main.js's
    createMicController()). Accepts a multipart audio file upload."""
    guard = _require_login()
    if guard:
        return guard

    if "audio" not in request.files:
        return jsonify(success=False, message="لم يتم استلام أي تسجيل صوتي"), 400

    audio_file = request.files["audio"]
    audio_bytes = audio_file.read()
    if not audio_bytes:
        return jsonify(success=False, message="التسجيل الصوتي فارغ"), 400

    lang = (request.form.get("lang") or "ar").strip().lower()[:2] or "ar"

    try:
        from ai import stt as ai_stt
    except Exception as e:
        return jsonify(success=False, message=f"خدمة التعرف على الصوت غير متاحة على الخادم: {e}"), 503

    try:
        text = ai_stt.transcribe(audio_bytes, filename=audio_file.filename or "audio.webm", language=lang)
    except ai_stt.STTUnavailable as e:
        return jsonify(success=False, message=f"تعذر التعرف على الصوت عبر الخادم: {e}"), 502
    except Exception as e:
        return jsonify(success=False, message=f"خطأ غير متوقع في التعرف على الصوت: {type(e).__name__}: {e}"), 500

    if not text:
        return jsonify(success=False, message="لم يتم التعرف على أي كلام في التسجيل"), 200

    return jsonify(success=True, text=text)


@app.route("/api/ai/diagnose")
def api_ai_diagnose():
    """Live, one-shot end-to-end OpenAI probe — unlike /health (which only
    checks config presence), this actually places a real Responses API call
    and reports the literal result. Use this to get a definitive answer to
    "is OpenAI actually reachable right now", including the exact
    underlying error (auth/network/model/rate-limit) if it isn't."""
    guard = _require_login()
    if guard:
        return guard

    if ai_engine is None:
        return jsonify(
            success=False,
            stage="module_import",
            message="ai_engine failed to import at startup — check the server's startup log for the NOTE printed when `from ai import ai_engine` failed.",
        ), 503

    try:
        from ai import responses as ai_responses
    except Exception as e:
        return jsonify(success=False, stage="import", message=f"{type(e).__name__}: {e}"), 503

    t0 = time.time()
    try:
        response = ai_responses.call_responses_api(
            system_prompt="أنت مساعد اختبار داخلي. أجب حصراً بكلمة واحدة: تم.",
            history=[],
            user_message="قل: تم",
            tools=[],
        )
        text = (getattr(response, "output_text", None) or "").strip()
        return jsonify(
            success=True,
            stage="openai_call",
            model=ai_responses.resolve_model(),
            response_text=text,
            elapsed_seconds=round(time.time() - t0, 2),
            message="OpenAI reached successfully — the AI fallback layer is fully working end-to-end.",
        )
    except ai_responses.OpenAIUnavailable as e:
        return jsonify(
            success=False,
            stage="openai_call",
            message=str(e),
            elapsed_seconds=round(time.time() - t0, 2),
        ), 502
    except Exception as e:
        return jsonify(
            success=False,
            stage="unexpected",
            message=f"{type(e).__name__}: {e}",
            elapsed_seconds=round(time.time() - t0, 2),
        ), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)
