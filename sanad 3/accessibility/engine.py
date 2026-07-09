#!/usr/bin/python3
"""Top-level entry point for Accessibility settings. app_server.py calls
only these functions — same convention as ai_engine/voice_tts/voice_auth's
engine modules. This one has no fail-closed concerns (it's not a security
gate); it just reads/writes preferences, so it stays intentionally thin."""

from . import settings as settings_mod


def get_settings(account: dict) -> dict:
    return settings_mod.get_settings(account)


def update_settings(account: dict, updates: dict) -> tuple[dict, str | None]:
    new_settings, error = settings_mod.validate_and_merge(account, updates)
    if error:
        return get_settings(account), error
    account["accessibility"] = new_settings
    return new_settings, None
