#!/usr/bin/python3
"""
Top-level entry point for the OpenAI fallback layer.

app_server.py calls ONLY get_ai_reply() from this module, and only after
the fast local layer (assistant_nlu.py + the session state machine) could
not confidently handle the message. This function NEVER raises — any
failure (no package, no key, network, rate limit, billing, timeout,
malformed response) results in {"unavailable": True}, and the caller is
expected to fall back to its existing default reply, exactly as before
this integration existed.

Security invariant: this module never touches pending_action, OTP, or
WebAuthn, and never writes to account.json/beneficiaries.json. It can only
return a *proposal* (tool call) or plain text for the caller to interpret.
"""

from . import conversation
from . import memory
from . import responses
from . import tools
from . import banking_tools

import logging

logger = logging.getLogger("sanad.ai.engine")
if not logger.handlers:
    import os as _os
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("[ai.engine] %(levelname)s: %(message)s"))
    logger.addHandler(_handler)
    _debug = _os.environ.get("SANAD_AI_DEBUG", "").strip().lower() in ("1", "true", "yes")
    logger.setLevel(logging.DEBUG if _debug else logging.INFO)


# General banking-knowledge questions are safe to cache across sessions
# (no account-specific data); a quick heuristic keeps us from caching
# anything that looks like it references the account or an active flow.
_NON_CACHEABLE_HINTS = ("رصيد", "حساب", "تحويل", "مستفيد", "بطاقتي", "balance", "my account")


def _is_cacheable(message: str) -> bool:
    low = (message or "").lower()
    return not any(hint in low for hint in _NON_CACHEABLE_HINTS)


def get_ai_reply(message: str, session, account: dict, current_page: str = "unknown", senior_mode: bool = False) -> dict:
    """Returns one of:
      {"unavailable": True}
      {"type": "tool", "tool": "...", "args": {...}}
      {"type": "text", "text": "..."}
    """
    message = (message or "").strip()
    if not message:
        return {"unavailable": True}

    cache_key = None
    if _is_cacheable(message):
        # senior_mode is part of the key: it changes the response tone
        # (simpler wording, shorter sentences), so a cached answer
        # generated for one mode must never be served to the other.
        cache_key = memory.normalize_cache_key(message) + ("|senior" if senior_mode else "|standard")
        cached = memory.cache_get(cache_key)
        if cached is not None:
            return {"type": "text", "text": cached}

    try:
        system_prompt, history = conversation.build_call_inputs(session, message, account, current_page, senior_mode=senior_mode)
        response = responses.call_responses_api(
            system_prompt=system_prompt,
            history=history,
            user_message=message,
            tools=banking_tools.TOOLS,
        )
        decision = tools.parse_response(response)
    except responses.OpenAIUnavailable as e:
        logger.info(f"AI fallback unavailable for this message, degrading to local NLU: {e}")
        return {"unavailable": True}
    except Exception as e:
        # Absolute last resort — never let an unexpected error from this
        # optional layer break the assistant.
        logger.warning(f"unexpected error in get_ai_reply(), degrading to local NLU: {type(e).__name__}: {e}")
        return {"unavailable": True}

    if decision["type"] == "text":
        memory.append_turn(session, message, decision["text"])
        if cache_key:
            memory.cache_set(cache_key, decision["text"])
    else:
        # Tool proposals aren't cached (they're context/account specific by
        # nature), but still recorded in conversation memory as a short
        # assistant-side note so follow-ups make sense.
        memory.append_turn(session, message, f"[اقتراح: {decision['tool']}]")

    return decision


def stream_ai_reply(message: str, session, account: dict, current_page: str = "unknown", senior_mode: bool = False):
    """Generator variant of get_ai_reply() for the streaming chat endpoint
    (see app_server.py's /api/assistant/stream). Yields dicts:

      {"kind": "delta", "text": "..."}            - a text chunk to display/append live
      {"kind": "done_text", "text": "..."}         - full text finished normally
      {"kind": "tool", "tool": "...", "args": {}}  - model wants a tool call; caller
                                                      must NOT execute it here — hand
                                                      back to the synchronous path that
                                                      reuses the exact same OTP/WebAuthn
                                                      gate as get_ai_reply()'s callers.
      {"kind": "unavailable"}                      - AI unusable; caller should fall back
                                                      exactly as get_ai_reply()'s
                                                      {"unavailable": True} case.

    Never raises — every failure mode yields {"kind": "unavailable"} instead,
    same defensive contract as get_ai_reply()."""
    message = (message or "").strip()
    if not message:
        yield {"kind": "unavailable"}
        return

    cache_key = None
    if _is_cacheable(message):
        cache_key = memory.normalize_cache_key(message) + ("|senior" if senior_mode else "|standard")
        cached = memory.cache_get(cache_key)
        if cached is not None:
            yield {"kind": "delta", "text": cached}
            yield {"kind": "done_text", "text": cached}
            return

    try:
        system_prompt, history = conversation.build_call_inputs(session, message, account, current_page, senior_mode=senior_mode)
        full_text_parts = []
        for event in responses.stream_responses_api(
            system_prompt=system_prompt, history=history, user_message=message, tools=banking_tools.TOOLS,
        ):
            event_type = getattr(event, "type", "")
            if event_type == "response.output_text.delta":
                delta = getattr(event, "delta", "") or ""
                if delta:
                    full_text_parts.append(delta)
                    yield {"kind": "delta", "text": delta}
            elif event_type == "response.completed":
                response_obj = getattr(event, "response", None)
                decision = tools.parse_response(response_obj) if response_obj is not None else None
                if decision and decision["type"] == "tool":
                    yield {"kind": "tool", "tool": decision["tool"], "args": decision["args"]}
                    return
                final_text = (decision["text"] if decision and decision["type"] == "text" else None) or "".join(full_text_parts)
                if final_text:
                    memory.append_turn(session, message, final_text)
                    if cache_key:
                        memory.cache_set(cache_key, final_text)
                    yield {"kind": "done_text", "text": final_text}
                else:
                    yield {"kind": "unavailable"}
                return
        # Stream ended without an explicit "completed" event (unexpected,
        # but salvage whatever text deltas did arrive rather than losing
        # a perfectly good answer).
        if full_text_parts:
            final_text = "".join(full_text_parts)
            memory.append_turn(session, message, final_text)
            if cache_key:
                memory.cache_set(cache_key, final_text)
            yield {"kind": "done_text", "text": final_text}
        else:
            yield {"kind": "unavailable"}
    except responses.OpenAIUnavailable as e:
        logger.info(f"AI streaming unavailable for this message, caller should redo via the local/blocking path: {e}")
        yield {"kind": "unavailable"}
    except Exception as e:
        # Same absolute last resort as get_ai_reply().
        logger.warning(f"unexpected error in stream_ai_reply(): {type(e).__name__}: {e}")
        yield {"kind": "unavailable"}
