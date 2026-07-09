#!/usr/bin/python3
"""Validation layer for Voice Authentication settings updates, called from
the /api/voice-auth/settings route. Kept separate from storage.py so the
"is this input valid" question and "how do we persist it" question don't
get tangled — same separation used in voice/settings.py."""

from . import storage


def apply_settings_update(account: dict, updates: dict) -> tuple[bool, str | None]:
    if "enabled" in updates:
        ok, error = storage.set_enabled(account, bool(updates["enabled"]))
        if not ok:
            return False, error

    if "threshold" in updates:
        try:
            threshold = float(updates["threshold"])
        except (TypeError, ValueError):
            return False, "قيمة الحساسية غير صحيحة"
        ok, error = storage.set_threshold(account, threshold)
        if not ok:
            return False, error

    return True, None
