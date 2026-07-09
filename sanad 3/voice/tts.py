#!/usr/bin/python3
"""
Top-level entry point for the voice subsystem. app_server.py calls ONLY the
functions in this module.

Fallback chain for a synthesis request:
    1. Cache (identical text+provider+voice+speed seen recently)
    2. The account's preferred provider (or the first available one if
       preference is "auto")
    3. The OTHER server-side provider, if the preferred one fails
    4. None / "browser" -> caller (Flask route) tells the client to use
       its own Web Speech API voice, exactly like before this subsystem
       existed. This path can NEVER throw — total unavailability of every
       server-side provider is an expected, handled outcome, not an error.

Security/compat note: this module only ever reads text that the CALLER
already decided is safe to speak (the assistant's own reply, or a fixed
system message). It never inspects account.json for anything beyond the
non-sensitive voice_settings block, and never touches pending_action/OTP/
WebAuthn/beneficiaries — completely orthogonal to those subsystems.
"""

from . import cache
from . import settings as voice_settings_mod
from . import streaming
from .providers import (
    EdgeTTSProvider, OpenAITTSProvider, BrowserFallbackProvider, TTSUnavailable,
)

import logging
import os

logger = logging.getLogger("sanad.voice.tts")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("[voice.tts] %(levelname)s: %(message)s"))
    logger.addHandler(_handler)
    _debug = os.environ.get("SANAD_VOICE_DEBUG", "").strip().lower() in ("1", "true", "yes")
    logger.setLevel(logging.DEBUG if _debug else logging.INFO)

MIME_TYPE = "audio/mpeg"

_provider_instances = {
    "edge": EdgeTTSProvider(),
    "openai": OpenAITTSProvider(),
    "browser": BrowserFallbackProvider(),
}


def _provider_chain(preferred: str):
    """Ordered list of provider instances to try, preferred first, always
    ending conceptually at 'browser' (handled by the caller, not tried
    here since it has no server-side audio)."""
    order = []
    if preferred == "edge":
        order = ["edge", "openai"]
    elif preferred == "openai":
        order = ["openai", "edge"]
    else:  # "auto" or "browser" or anything unrecognized -> try both, edge first
        order = ["edge", "openai"]

    chain = []
    for name in order:
        provider = _provider_instances[name]
        available = provider.is_available()
        logger.debug(f"provider '{name}' is_available()={available}")
        if available:
            chain.append(provider)
    return chain


def _detect_lang(text: str) -> str:
    for ch in text or "":
        if "\u0600" <= ch <= "\u06FF":
            return "ar"
    return "en"


def get_settings(account: dict) -> dict:
    return voice_settings_mod.get_voice_settings(account)


def update_settings(account: dict, updates: dict):
    return voice_settings_mod.validate_and_merge(account, updates)


def can_synthesize(account: dict) -> bool:
    """True if at least one server-side provider is actually available for
    this account's preferred setting right now. Used by the streaming
    route to decide, BEFORE starting a chunked response, whether to signal
    the browser fallback instead of opening a stream that would otherwise
    silently yield zero bytes."""
    vsettings = get_settings(account)
    preferred = vsettings.get("provider", "auto")
    if preferred == "browser":
        return False
    return len(_provider_chain(preferred)) > 0


def synthesize(text: str, account: dict, lang: str = None):
    """Returns (audio_bytes, mime_type) on success, or None if every
    server-side provider is unavailable (caller should fall back to the
    browser's own voice). Never raises."""
    text = (text or "").strip()
    if not text:
        return None

    vsettings = get_settings(account)
    lang = lang or _detect_lang(text)
    preferred = vsettings.get("provider", "auto")

    if preferred == "browser":
        return None

    for provider in _provider_chain(preferred):
        voice = voice_settings_mod.resolve_voice_name(vsettings, provider.name, lang)
        speed = float(vsettings.get("speed", 1.0))
        key = cache.make_key(text, provider.name, voice, speed)

        cached = cache.get(key)
        if cached is not None:
            return cached, MIME_TYPE

        try:
            audio = provider.synthesize(text, voice, speed)
        except TTSUnavailable as e:
            logger.warning(f"provider '{provider.name}' failed, trying next in chain: {e}")
            continue
        except Exception as e:
            logger.warning(f"provider '{provider.name}' raised an unexpected error, trying next in chain: {type(e).__name__}: {e}")
            continue

        if audio:
            cache.set(key, audio)
            return audio, MIME_TYPE

    logger.info(f"no provider produced audio for provider preference='{preferred}' — falling back to browser voice")
    return None


def stream_synthesize(text: str, account: dict, lang: str = None):
    """Generator version for the streaming endpoint. Yields (chunk_bytes)
    values. Raises TTSUnavailable only once no provider in the chain could
    even start — the Flask route catches that and reports the browser
    fallback, exactly like synthesize() returning None."""
    text = (text or "").strip()
    if not text:
        raise TTSUnavailable("empty text")

    vsettings = get_settings(account)
    lang = lang or _detect_lang(text)
    preferred = vsettings.get("provider", "auto")

    if preferred == "browser":
        raise TTSUnavailable("provider set to browser")

    chain = _provider_chain(preferred)
    last_error = None
    for provider in chain:
        voice = voice_settings_mod.resolve_voice_name(vsettings, provider.name, lang)
        speed = float(vsettings.get("speed", 1.0))
        key = cache.make_key(text, provider.name, voice, speed)

        cached = cache.get(key)
        if cached is not None:
            yield cached
            return

        collected = []
        try:
            for chunk in streaming.stream_from_provider(provider, text, voice, speed):
                collected.append(chunk)
                yield chunk
            if collected:
                cache.set(key, b"".join(collected))
            return
        except TTSUnavailable as e:
            last_error = e
            logger.warning(f"provider '{provider.name}' failed during streaming, trying next in chain: {e}")
            continue

    logger.info(f"no provider could stream audio for provider preference='{preferred}' — falling back to browser voice. last error: {last_error}")
    raise TTSUnavailable(str(last_error) if last_error else "no provider available")
