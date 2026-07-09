#!/usr/bin/python3
"""
Small in-process TTS audio cache. Repeated phrases (confirmation prompts,
common banking answers, "تم تفعيل...") are common in a voice assistant, and
synthesis is the single slowest part of the voice pipeline — caching the
raw audio bytes for an identical (text, provider, voice, speed) combination
avoids paying that cost twice.

Deliberately process-local and in-memory (no disk/DB) — this is a cache,
not a store; losing it on restart is fine and correct.
"""

import hashlib
import threading
import time

_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, bytes]] = {}
_TTL_SECONDS = 1800  # 30 minutes - long enough to help a single demo session
_MAX_ENTRIES = 100
_MAX_BYTES_PER_ENTRY = 5 * 1024 * 1024  # don't cache absurdly long clips


def make_key(text: str, provider: str, voice: str, speed: float) -> str:
    raw = f"{provider}|{voice}|{speed:.2f}|{(text or '').strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get(key: str) -> bytes | None:
    with _LOCK:
        entry = _CACHE.get(key)
        if not entry:
            return None
        ts, data = entry
        if time.time() - ts > _TTL_SECONDS:
            _CACHE.pop(key, None)
            return None
        return data


def set(key: str, data: bytes) -> None:
    if not data or len(data) > _MAX_BYTES_PER_ENTRY:
        return
    with _LOCK:
        if len(_CACHE) >= _MAX_ENTRIES:
            oldest_key = min(_CACHE, key=lambda k: _CACHE[k][0])
            _CACHE.pop(oldest_key, None)
        _CACHE[key] = (time.time(), data)


def clear() -> None:
    with _LOCK:
        _CACHE.clear()


def stats() -> dict:
    with _LOCK:
        return {"entries": len(_CACHE), "total_bytes": sum(len(v) for _, v in _CACHE.values())}
