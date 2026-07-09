#!/usr/bin/python3
"""
Accessibility preferences (Settings -> Accessibility), persisted in
account.json under account["accessibility"] — same pattern as
account["voice_settings"] and account["voice_auth"].

Deliberately does NOT store speech rate or preferred voice: those already
live in account["voice_settings"] (voice/settings.py, Step 3) and are
reused as-is from the Accessibility page rather than duplicated here.
"""

DEFAULT_ACCESSIBILITY_SETTINGS = {
    "senior_mode": False,
    "high_contrast": False,
    "auto_read_screen": False,   # read the current page automatically on load
    "read_notifications": True,
    "read_errors": True,
    "read_success": True,
    "read_balances": True,
    "read_transactions": True,
    "read_otp_instructions": True,
}

_BOOL_FIELDS = set(DEFAULT_ACCESSIBILITY_SETTINGS.keys())


def get_settings(account: dict) -> dict:
    stored = account.get("accessibility") or {}
    merged = dict(DEFAULT_ACCESSIBILITY_SETTINGS)
    merged.update({k: v for k, v in stored.items() if k in DEFAULT_ACCESSIBILITY_SETTINGS})
    return merged


def validate_and_merge(account: dict, updates: dict) -> tuple[dict, str | None]:
    current = get_settings(account)
    new = dict(current)

    for field in _BOOL_FIELDS:
        if field in updates:
            new[field] = bool(updates[field])

    unknown = set(updates.keys()) - _BOOL_FIELDS
    if unknown:
        return current, f"إعدادات غير معروفة: {', '.join(sorted(unknown))}"

    return new, None
