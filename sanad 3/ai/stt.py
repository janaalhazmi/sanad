#!/usr/bin/python3
"""
Server-side speech-to-text fallback (OpenAI Whisper), used ONLY when the
browser's native SpeechRecognition is unavailable or fails to actually
work (see static/js/stt_fallback.js + main.js's createMicController(),
which decide when to invoke this). Reuses the same OPENAI_API_KEY as the
AI fallback/TTS layers.

Never raises past this module's boundary in a way that could crash the
request — every failure mode is normalized into STTUnavailable so the
caller (app_server.py's /api/stt route) can return a clear JSON error.
"""

import io
import os


class STTUnavailable(Exception):
    """Raised for any condition that means server-side STT can't run right
    now (missing package/key, network error, empty/invalid audio)."""


_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise STTUnavailable("OPENAI_API_KEY is not set")
    try:
        from openai import OpenAI
    except ImportError as e:
        raise STTUnavailable(f"openai package not installed: {e}")
    try:
        _client = OpenAI(api_key=api_key, timeout=20)
    except Exception as e:
        raise STTUnavailable(f"failed to construct OpenAI client: {e}")
    return _client


def is_available() -> bool:
    if not os.environ.get("OPENAI_API_KEY"):
        return False
    try:
        import openai  # noqa: F401
        return True
    except ImportError:
        return False


def transcribe(audio_bytes: bytes, filename: str = "audio.webm", language: str = "ar") -> str:
    """Returns the transcribed text (possibly empty if truly silent audio).
    Raises STTUnavailable on any failure."""
    if not audio_bytes:
        raise STTUnavailable("empty audio payload")

    client = _get_client()
    file_obj = io.BytesIO(audio_bytes)
    file_obj.name = filename  # the SDK reads this to infer the audio format

    try:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=file_obj,
            language=language or "ar",
        )
    except Exception as e:
        raise STTUnavailable(f"whisper transcription failed: {type(e).__name__}: {e}")

    return (getattr(result, "text", None) or "").strip()
