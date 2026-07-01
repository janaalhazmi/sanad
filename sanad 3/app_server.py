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
import base64
import hashlib
import time
import uuid
import threading

from otp import OTPManager
from transfer import TransferManager
from beneficiary import BeneficiaryManager
import assistant_nlu as nlu

app = Flask(__name__)
app.secret_key = "sanad-demo-secret-key-change-me"

# Static assets rarely change during a demo session; let the browser cache
# them instead of re-fetching on every navigation (real, measurable speed win).
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600

try:
    # Optional: only needed if the frontend is served from a different origin.
    from flask_cors import CORS
    CORS(app, supports_credentials=True)
except ImportError:
    pass

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


# -----------------------------------------------------------------------
# Page routes
# -----------------------------------------------------------------------
@app.route("/")
def login():
    if is_logged_in():
        return redirect(url_for("dashboard"))
    return render_template("login.html", account=load_account())


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
    session["webauthn_reg_challenge"] = _b64url_encode(challenge)

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
    expected_challenge = session.get("webauthn_reg_challenge")
    if not expected_challenge:
        return jsonify(success=False, message="انتهت صلاحية الطلب، حاول مرة أخرى"), 400

    try:
        client_data_bytes = _b64url_decode(data["clientDataJSON"])
    except Exception:
        return jsonify(success=False, message="بيانات غير صالحة"), 400

    ok, err = _verify_client_data(client_data_bytes, "webauthn.create", expected_challenge)
    if not ok:
        return jsonify(success=False, message=err), 400

    account = load_account()
    account["webauthn"] = {
        "id": data.get("id"),
        "public_key": data.get("publicKey"),
        "alg": data.get("alg"),
    }
    save_account(account)
    session.pop("webauthn_reg_challenge", None)

    return jsonify(success=True, message="تم تفعيل الدخول بالبصمة على هذا الجهاز")


@app.route("/api/webauthn/login/options", methods=["POST"])
def api_webauthn_login_options():
    account = load_account()
    cred = account.get("webauthn")
    if not cred:
        return jsonify(success=False, message="الدخول بالبصمة غير مفعّل"), 404

    challenge = os.urandom(32)
    session["webauthn_login_challenge"] = _b64url_encode(challenge)

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
    cred = account.get("webauthn")
    expected_challenge = session.get("webauthn_login_challenge")

    ok, err = _verify_webauthn_assertion(data, expected_challenge, cred)
    if not ok:
        return jsonify(success=False, message=err), 400

    session["logged_in"] = True
    session["phone"] = account.get("phone")
    session.pop("webauthn_login_challenge", None)

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
    return jsonify(
        success=True,
        type=pending["type"],
        description=_describe_pending_action(pending),
        webauthn_available=bool(account.get("webauthn")),
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

    result = _execute_pending_action()
    status = 200 if result.get("success") else 400
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
    session["action_webauthn_challenge"] = _b64url_encode(challenge)

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
    expected_challenge = session.get("action_webauthn_challenge")

    ok, err = _verify_webauthn_assertion(data, expected_challenge, cred)
    if not ok:
        return jsonify(success=False, message=err), 400

    session.pop("action_webauthn_challenge", None)
    result = _execute_pending_action()
    status = 200 if result.get("success") else 400
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
}


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
def _start_transfer_flow(message, context, beneficiaries):
    amount, name_hint = nlu.extract_transfer(message)
    amount = amount or context.get("entered_amount")
    name_hint = name_hint or context.get("selected_beneficiary")
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


@app.route("/api/assistant", methods=["POST"])
def api_assistant():
    data = request.json or {}
    message = (data.get("message") or "").strip()
    context = data.get("context") or {}

    if not message:
        return jsonify(response="لم أسمعك جيداً، هل يمكنك إعادة المحاولة؟", action=None)

    # Pre-login (e.g. on the login screen itself): the assistant can still
    # help with navigation/reading the screen, but must never answer
    # account-specific questions before authentication.
    if not is_logged_in():
        nav = nlu.detect_navigation(message)
        if nav == "read_screen":
            return jsonify(response="سأقرأ محتوى الشاشة الآن.", action={"readScreen": True})
        if nav in ("cancel", "close"):
            return jsonify(response="تم.", action=None)
        return jsonify(
            response="الرجاء تسجيل الدخول أولاً حتى أتمكن من مساعدتك في حسابك. يمكنك قول \"اقرأ الشاشة\" للمساعدة في تسجيل الدخول.",
            action=None,
        )

    account = load_account()
    state = session.get("assistant_state")

    # Cancel always breaks out of any active flow first.
    if nlu.detect_navigation(message) == "cancel" and (state or session.get("pending_action")):
        result = _handle_navigation("cancel")
        return jsonify(**result)

    # A bare "no" while specifically at a yes/no confirmation step cancels
    # that flow. (Other stages, like the beneficiary nickname step, use "لا"
    # to mean something else — "skip" — so this only applies to "confirm".)
    if state and state.get("stage") == "confirm" and nlu.is_negative(message):
        _clear_assistant_state()
        return jsonify(response="تم إلغاء العملية.", action=None)

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
                return jsonify(**result)

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
            return jsonify(**result)

    # Start a new add-beneficiary conversation.
    if nlu.looks_like_add_beneficiary(message):
        result = _start_add_beneficiary_flow()
        return jsonify(**result)

    # Start a new transfer conversation.
    intent = nlu.detect_intent(message)
    if intent == "transfer" or nlu.looks_like_transfer(message):
        result = _start_transfer_flow(message, context, load_beneficiaries())
        if result is not None:
            return jsonify(**result)

    # Voice navigation commands.
    nav = nlu.detect_navigation(message)
    if nav:
        result = _handle_navigation(nav)
        if result is not None:
            return jsonify(**result)

    # Regular single-turn intents.
    reply_fn = INTENT_REPLIES.get(intent)
    response_text = reply_fn(account) if reply_fn else (
        "عذراً، لم أفهم طلبك. يمكنك سؤالي عن الرصيد أو البطاقات أو التحويل أو إضافة مستفيد أو الانتقال بين الصفحات."
    )
    action = {"navigate": INTENT_ACTIONS[intent]} if intent in INTENT_ACTIONS else None

    return jsonify(response=response_text, action=action, intent=intent)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
