#!/usr/bin/python3
"""
Top-level entry point for Voice Authentication. app_server.py calls ONLY
the functions in this module.

THE CRITICAL POLICY DECISION LIVES HERE: unlike ai_engine.get_ai_reply()
and voice_tts.synthesize() (Steps 2-3), which return a "not available"
signal that callers turn into a graceful, silent fallback, this module's
verify_for_pending_action() NEVER lets "the model isn't installed" look
the same as "verification succeeded" or get silently skipped. If a user
has enabled Voice Authentication, an unavailable provider must BLOCK the
action. Availability failures and negative-verdict rejections both come
back as accepted=False with a message — the caller doesn't need to
distinguish them to do the right thing (never execute), but the message
differs so the user understands why.

Security invariant: this module never calls _execute_pending_action() or
anything resembling it. It only ever returns an accept/reject decision;
app_server.py remains the sole place that can actually execute a pending
action, exactly as before this feature existed.
"""

from . import storage
from . import enrollment
from . import verification
from . import embeddings as emb_mod


def is_available() -> bool:
    """True if at least one embedding provider is installed right now."""
    return emb_mod.first_available_provider() is not None


def is_enrolled(account: dict) -> bool:
    return storage.is_enrolled(account)


def is_enabled(account: dict) -> bool:
    return storage.is_enabled(account)


def get_status(account: dict) -> dict:
    profile = storage.get_profile(account)
    return {
        "available": is_available(),
        "enrolled": bool(profile.get("enrolled")),
        "enabled": bool(profile.get("enabled")),
        "provider": profile.get("provider"),
        "sample_count": profile.get("sample_count", 0),
        "threshold": profile.get("threshold", 0.75),
        "enrolled_at": profile.get("enrolled_at"),
    }


def enroll(account: dict, audio_samples: list) -> tuple[bool, str | None]:
    """Performs enrollment and persists it into `account` (caller is
    responsible for save_account()). Returns (ok, error_message)."""
    try:
        result = enrollment.enroll(audio_samples)
    except enrollment.EnrollmentError as e:
        return False, str(e)
    except Exception as e:
        # Absolute last resort — enrollment failing should never crash a
        # request; it just means enrollment didn't succeed this time.
        return False, f"تعذر إتمام التسجيل الصوتي: {e}"

    storage.save_enrollment(account, result["provider"], result["embeddings"], result["centroid"])
    return True, None


def delete(account: dict) -> None:
    storage.delete_profile(account)


def update_settings(account: dict, updates: dict) -> tuple[bool, str | None]:
    from . import settings as settings_mod
    return settings_mod.apply_settings_update(account, updates)


def verify_for_pending_action(account: dict, audio_sample: bytes) -> dict:
    """The ONLY function that matters for the security gate. Returns:
        {"accepted": bool, "confidence": float|None, "message": str|None}
    `accepted=False` covers BOTH "voice didn't match" (a normal, valid
    negative verdict) AND "couldn't even check" (provider down, bad
    profile, bad audio) — fail-closed either way. The message explains
    which case it was, for the user/logs, but the caller's job (block
    execution) is identical regardless."""
    profile = storage.get_profile(account)

    if not storage.is_enabled(account):
        # Should never be called in this state (engine callers check
        # is_enabled() before even offering this step) — but if it
        # somehow is, fail closed rather than assume "not required".
        return {"accepted": False, "confidence": None, "message": "التحقق الصوتي غير مفعّل على هذا الحساب"}

    try:
        result = verification.verify(audio_sample, profile)
    except verification.VerificationError as e:
        return {"accepted": False, "confidence": None, "message": str(e)}
    except Exception as e:
        return {"accepted": False, "confidence": None, "message": f"تعذر التحقق من الصوت: {e}"}

    if result["accepted"]:
        return {"accepted": True, "confidence": result["confidence"], "message": "تم التحقق من الصوت بنجاح"}
    return {
        "accepted": False,
        "confidence": result["confidence"],
        "message": "لم يتم التعرف على صوتك، حاول مرة أخرى أو استخدم طريقة تحقق أخرى",
    }
