#!/usr/bin/python3
"""
Streaming synthesis: yields audio chunks as they become available instead
of waiting for the whole clip, so the client can start playback sooner.
Layered on top of tts.py's provider-resolution logic — this module doesn't
know about caching or account settings, just "given a resolved provider,
voice, and speed, yield chunks (or raise TTSUnavailable)".
"""

from .providers import TTSUnavailable


def stream_from_provider(provider, text: str, voice: str, speed: float):
    """Thin pass-through with one guarantee callers rely on: if the
    provider raises partway through iteration (e.g. the connection drops
    mid-clip), that's surfaced as TTSUnavailable too, not a raw/unrelated
    exception, so the Flask route can react consistently either way."""
    try:
        for chunk in provider.stream(text, voice, speed):
            if chunk:
                yield chunk
    except TTSUnavailable:
        raise
    except Exception as e:
        raise TTSUnavailable(f"stream interrupted: {e}")


def collect_stream(provider, text: str, voice: str, speed: float) -> bytes:
    """Convenience: fully materialize a streamed clip (used when caching a
    result, or when the caller wants one complete byte string)."""
    return b"".join(stream_from_provider(provider, text, voice, speed))
