#!/usr/bin/python3
"""
Per-account voice preferences (Settings -> Voice), persisted in
account.json exactly like every other setting in this app (name, phone,
password, webauthn) — no separate DB/file, no migration needed, and it's
picked up immediately on the next request since account.json is always
re-read fresh (same pattern api_settings_update() already uses).
"""

import re

DEFAULT_VOICE_SETTINGS = {
    "provider": "auto",       # "auto" | "edge" | "openai" | "browser"
    "speed": 1.0,             # 0.5 - 2.0
    "volume": 1.0,            # 0.0 - 1.0 (applied client-side; providers
                               # don't control loudness server-side)
    "gender": "male",         # "male" | "female" -> selects which named
                               # voice below is used per provider/language
    "arabic_voice": None,     # explicit override; None = derive from gender
    "english_voice": None,    # explicit override; None = derive from gender
}

VALID_PROVIDERS = {"auto", "edge", "openai", "browser"}
VALID_GENDERS = {"male", "female"}


def get_voice_settings(account: dict) -> dict:
    stored = account.get("voice_settings") or {}
    merged = dict(DEFAULT_VOICE_SETTINGS)
    merged.update({k: v for k, v in stored.items() if k in DEFAULT_VOICE_SETTINGS})
    return merged


def validate_and_merge(account: dict, updates: dict) -> tuple[dict, str | None]:
    """Returns (new_voice_settings, error_message). error_message is None on
    success. Never raises — bad input just gets rejected with a message."""
    current = get_voice_settings(account)
    new = dict(current)

    if "provider" in updates:
        provider = str(updates["provider"] or "auto").strip().lower()
        if provider not in VALID_PROVIDERS:
            return current, f"مزوّد صوت غير معروف: {provider}"
        new["provider"] = provider

    if "speed" in updates:
        try:
            speed = float(updates["speed"])
        except (TypeError, ValueError):
            return current, "قيمة سرعة الصوت غير صحيحة"
        if not (0.5 <= speed <= 2.0):
            return current, "سرعة الصوت يجب أن تكون بين 0.5 و 2.0"
        new["speed"] = speed

    if "volume" in updates:
        try:
            volume = float(updates["volume"])
        except (TypeError, ValueError):
            return current, "قيمة مستوى الصوت غير صحيحة"
        if not (0.0 <= volume <= 1.0):
            return current, "مستوى الصوت يجب أن يكون بين 0.0 و 1.0"
        new["volume"] = volume

    if "gender" in updates:
        gender = str(updates["gender"] or "male").strip().lower()
        if gender not in VALID_GENDERS:
            return current, f"جنس الصوت غير معروف: {gender}"
        new["gender"] = gender

    if "arabic_voice" in updates:
        new["arabic_voice"] = (str(updates["arabic_voice"]).strip() or None) if updates["arabic_voice"] else None

    if "english_voice" in updates:
        new["english_voice"] = (str(updates["english_voice"]).strip() or None) if updates["english_voice"] else None

    return new, None


# A real Microsoft Edge/Azure neural voice name always looks like
# "xx-XX-NameNeural" (locale-locale-Name + "Neural"). This lets us reject a
# stale/invalid explicit override (e.g. a leftover literal "default" from
# an older buggy version of this setting) instead of blindly handing it to
# edge-tts and letting the whole request fail.
_VALID_VOICE_NAME_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}-\w+Neural$")


def _is_valid_explicit_voice(name) -> bool:
    return bool(name) and bool(_VALID_VOICE_NAME_RE.match(str(name)))


def resolve_voice_name(voice_settings: dict, provider_name: str, lang: str) -> str:
    """Turns (gender/explicit override) into an actual named voice for the
    given provider + language ('ar' or 'en'). `provider_name` here is each
    provider's own `.name` attribute (e.g. EdgeTTSProvider.name ==
    "edge_tts", OpenAITTSProvider.name == "openai") — NOT the "edge"/"openai"
    keys used for the user-facing provider *preference* setting/_provider_chain
    lookup. These two naming schemes must stay in sync with voice/providers.py."""
    from . import providers as p

    is_arabic = lang.startswith("ar")
    explicit = voice_settings.get("arabic_voice" if is_arabic else "english_voice")
    # Only trust an explicit override for edge-tts if it actually looks like
    # a real Edge voice name — OpenAI's voice names ("onyx", "shimmer", ...)
    # don't follow this pattern and are validated separately by that branch
    # below, so this check only ever rejects garbage, never a legitimate
    # OpenAI voice choice.
    if explicit and (provider_name != "edge_tts" or _is_valid_explicit_voice(explicit)):
        return explicit

    is_female = voice_settings.get("gender") == "female"

    if provider_name == "edge_tts":
        if is_arabic:
            return p.DEFAULT_AR_VOICE_EDGE_FEMALE if is_female else p.DEFAULT_AR_VOICE_EDGE
        return p.DEFAULT_EN_VOICE_EDGE_FEMALE if is_female else p.DEFAULT_EN_VOICE_EDGE

    if provider_name == "openai":
        # OpenAI's voice names aren't gendered/language-specific the same
        # way; a small curated pair is enough for "male-ish"/"female-ish".
        return "onyx" if not is_female else "shimmer"

    # Fallback should never actually be handed to a real synthesis call
    # (the "browser" provider never reaches resolve_voice_name — see
    # voice/tts.py's _provider_chain(), which only ever builds a chain out
    # of "edge"/"openai"). If some future/unknown provider name DOES reach
    # here, default to a real, valid Arabic Edge voice rather than the
    # placeholder string "default", which is not a valid voice name for
    # ANY provider and would otherwise cause a hard synthesis failure.
    if is_arabic:
        return p.DEFAULT_AR_VOICE_EDGE_FEMALE if is_female else p.DEFAULT_AR_VOICE_EDGE
    return p.DEFAULT_EN_VOICE_EDGE_FEMALE if is_female else p.DEFAULT_EN_VOICE_EDGE
