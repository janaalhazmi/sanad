#!/usr/bin/python3
"""
Two independent, small memory stores for the AI fallback layer:

1. Per-session conversation history (kept in Flask's session, capped short
   so the cookie stays small) — gives the AI real follow-up context
   ("حول لأحمد" -> "٢٠٠" -> "أكيد") across turns that reach it.

2. A tiny process-local TTL cache for repeated identical Q&A (e.g. several
   users/demo runs asking "ما هي رسوم التحويل الدولي؟") to cut latency and
   API cost. Deliberately NOT used for anything that could differ per
   account state (balance, transactions) — only for stable, general
   banking-knowledge questions, keyed on the normalized question text.
"""

import time
import threading

MAX_HISTORY_TURNS = 6  # user+assistant pairs kept, oldest dropped first


def get_history(session) -> list:
    return list(session.get("ai_history") or [])


def append_turn(session, user_message: str, assistant_message: str) -> None:
    history = get_history(session)
    history.append({"role": "user", "content": user_message})
    history.append({"role": "assistant", "content": assistant_message})
    # Keep only the most recent N turns (2 messages per turn).
    history = history[-(MAX_HISTORY_TURNS * 2):]
    session["ai_history"] = history


def clear_history(session) -> None:
    session["ai_history"] = []


# ---------------------------------------------------------------------------
# Process-local TTL response cache (general Q&A only — see module docstring)
# ---------------------------------------------------------------------------
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL_SECONDS = 600
_CACHE_MAX_ENTRIES = 256


def cache_get(key: str):
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if not entry:
            return None
        ts, value = entry
        if time.time() - ts > _CACHE_TTL_SECONDS:
            _CACHE.pop(key, None)
            return None
        return value


def cache_set(key: str, value: str) -> None:
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX_ENTRIES:
            # Cheap eviction: drop the oldest entry.
            oldest_key = min(_CACHE, key=lambda k: _CACHE[k][0])
            _CACHE.pop(oldest_key, None)
        _CACHE[key] = (time.time(), value)


def normalize_cache_key(message: str) -> str:
    return " ".join((message or "").strip().lower().split())
