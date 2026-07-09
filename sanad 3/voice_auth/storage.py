#!/usr/bin/python3
"""
Persistence for the Voice Authentication profile, stored under
account["voice_auth"] — same pattern as account["webauthn"].

CRITICAL: this module only ever stores embeddings (small numeric vectors)
plus a handful of metadata fields — never raw audio. Anything holding raw
audio bytes must be discarded by the caller (enrollment.py/verification.py)
before this module is ever invoked with a profile to save.
"""

import time


def get_profile(account: dict) -> dict:
    """Returns the stored profile dict, or a default 'not enrolled' shape.
    Never returns None so callers don't need extra guards everywhere."""
    stored = account.get("voice_auth")
    if not stored:
        return {
            "enabled": False,
            "enrolled": False,
            "provider": None,
            "embeddings": [],
            "centroid": None,
            "threshold": 0.75,
            "sample_count": 0,
            "enrolled_at": None,
        }
    return stored


def is_enrolled(account: dict) -> bool:
    profile = get_profile(account)
    return bool(profile.get("enrolled") and profile.get("centroid"))


def is_enabled(account: dict) -> bool:
    """Enrolled AND the user has explicitly toggled it on. Enrollment
    alone does not activate it as an auth gate — matches the existing
    WebAuthn UX where enrolling and "using it for logins" are the same
    toggle, but here we keep them as two explicit states since voice auth
    is an ADDITIONAL factor stacked on top of OTP/WebAuthn, not a
    replacement, and users should be able to enroll now / enable later."""
    profile = get_profile(account)
    return bool(profile.get("enabled") and is_enrolled({"voice_auth": profile}))


def save_enrollment(account: dict, provider_name: str, embeddings: list, centroid: list) -> dict:
    """Persists a fresh enrollment (or re-enrollment — always a full
    replace, never an append, so a bad re-recording can't dilute/poison a
    previously good profile). `embeddings`/`centroid` must already be
    plain lists of floats (JSON-serializable), not numpy arrays."""
    existing = get_profile(account)
    profile = {
        "enabled": existing.get("enabled", False),  # re-enrolling doesn't silently turn it on
        "enrolled": True,
        "provider": provider_name,
        "embeddings": embeddings,
        "centroid": centroid,
        "threshold": existing.get("threshold", 0.75),
        "sample_count": len(embeddings),
        "enrolled_at": time.time(),
    }
    account["voice_auth"] = profile
    return profile


def set_enabled(account: dict, enabled: bool) -> tuple[bool, str | None]:
    profile = get_profile(account)
    if enabled and not is_enrolled({"voice_auth": profile}):
        return False, "يجب تسجيل بصمة صوتك أولاً قبل تفعيلها"
    profile["enabled"] = bool(enabled)
    account["voice_auth"] = profile
    return True, None


def set_threshold(account: dict, threshold: float) -> tuple[bool, str | None]:
    if not (0.5 <= threshold <= 0.95):
        return False, "قيمة الحساسية يجب أن تكون بين 0.5 و 0.95"
    profile = get_profile(account)
    profile["threshold"] = threshold
    account["voice_auth"] = profile
    return True, None


def delete_profile(account: dict) -> None:
    account.pop("voice_auth", None)
