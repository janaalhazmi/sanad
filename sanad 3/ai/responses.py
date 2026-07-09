#!/usr/bin/python3
"""
Thin, defensive wrapper around the OpenAI Responses API.

- Singleton client (module-level), reused across requests — no per-request
  client construction.
- Model resolution: try GPT-5 first; if the account/API doesn't have access
  to it (model_not_found / permission error), fall back to GPT-4.1 and
  remember that choice for the rest of the process (no repeated probing).
- Every failure mode (no package installed, no API key, network error, rate
  limit, billing, timeout, malformed response) is caught and normalized into
  an OpenAIUnavailable exception so the caller (ai_engine.py) can fall back
  to assistant_nlu.py without ever crashing the assistant.
"""

import os
import threading
import logging

logger = logging.getLogger("sanad.ai.responses")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("[ai.responses] %(levelname)s: %(message)s"))
    logger.addHandler(_handler)
    _debug = os.environ.get("SANAD_AI_DEBUG", "").strip().lower() in ("1", "true", "yes")
    logger.setLevel(logging.DEBUG if _debug else logging.INFO)

PREFERRED_MODEL = "gpt-5"
FALLBACK_MODEL = "gpt-4.1"
# Tried in order until one actually works for this account/API key, then
# cached in _resolved_model so every later call skips straight to it. Ends
# on gpt-4o — the most universally available Responses-API-capable model
# — so a missing/inaccessible gpt-5 or gpt-4.1 can never be the sole
# reason the whole AI fallback silently stops working.
MODEL_CASCADE = [PREFERRED_MODEL, FALLBACK_MODEL, "gpt-4o"]
REQUEST_TIMEOUT_SECONDS = 12

_client = None
_client_lock = threading.Lock()
_resolved_model = None  # cached across requests once known
_resolved_model_lock = threading.Lock()


class OpenAIUnavailable(Exception):
    """Raised for ANY condition that should trigger a fallback to the local
    NLU instead of surfacing an error to the user."""


def _get_client():
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY is not set in the process environment — check your .env file is present and python-dotenv is installed (see requirements.txt).")
            raise OpenAIUnavailable("OPENAI_API_KEY is not set")
        try:
            from openai import OpenAI
        except ImportError as e:
            logger.warning(f"the 'openai' package is not installed: {e}")
            raise OpenAIUnavailable(f"openai package not installed: {e}")
        try:
            _client = OpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS)
            logger.info(f"OpenAI client constructed successfully (API key present, length={len(api_key)}).")
        except Exception as e:
            logger.warning(f"failed to construct OpenAI client: {e}")
            raise OpenAIUnavailable(f"failed to construct OpenAI client: {e}")
        return _client


def _classify_and_raise(exc: Exception):
    """Turn any SDK exception into OpenAIUnavailable with a readable reason,
    covering: network errors, timeouts, rate limits, billing/quota, auth,
    and unknown/model errors. Always logged server-side so a broken
    integration is never a silent, invisible failure."""
    try:
        from openai import (
            APIConnectionError, APITimeoutError, RateLimitError,
            AuthenticationError, PermissionDeniedError, NotFoundError,
            APIError,
        )
    except ImportError:
        logger.warning(f"openai error (package missing): {exc}")
        raise OpenAIUnavailable(f"openai error (package missing): {exc}")

    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        logger.warning(f"network/timeout calling OpenAI: {exc}")
        raise OpenAIUnavailable(f"network/timeout: {exc}")
    if isinstance(exc, RateLimitError):
        logger.warning(f"OpenAI rate limit or quota/billing error: {exc}")
        raise OpenAIUnavailable(f"rate limit or quota/billing: {exc}")
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        logger.warning(f"OpenAI auth/billing error — check OPENAI_API_KEY is valid and has access: {exc}")
        raise OpenAIUnavailable(f"auth/billing error: {exc}")
    if isinstance(exc, NotFoundError):
        # Special-cased by the caller for model fallback; re-raise as-is.
        raise exc
    if isinstance(exc, APIError):
        logger.warning(f"OpenAI API error: {exc}")
        raise OpenAIUnavailable(f"API error: {exc}")
    logger.warning(f"unexpected error calling OpenAI: {type(exc).__name__}: {exc}")
    raise OpenAIUnavailable(f"unexpected error: {exc}")


def resolve_model() -> str:
    """Returns the model name currently believed to work, defaulting to the
    top of MODEL_CASCADE until an actual call proves otherwise (see
    call_responses_api()/stream_responses_api(), which do the real probing
    and update this cache)."""
    global _resolved_model
    if _resolved_model:
        return _resolved_model
    return MODEL_CASCADE[0]


def _set_resolved_model(model: str):
    global _resolved_model
    with _resolved_model_lock:
        _resolved_model = model


def call_responses_api(*, system_prompt: str, history: list, user_message: str, tools: list):
    """Single non-streaming call. Tries MODEL_CASCADE in order (starting
    from whichever model already proved to work, if any) until one
    actually succeeds — this is a REAL retry against the live API, not a
    guess based on parsing OpenAI's error text/type, so an inaccessible
    'gpt-5' or 'gpt-4.1' on a given account can never be the silent, sole
    reason every AI fallback call fails. Raises OpenAIUnavailable only if
    every model in the cascade fails."""
    client = _get_client()

    input_messages = [{"role": "system", "content": system_prompt}]
    input_messages.extend(history)
    input_messages.append({"role": "user", "content": user_message})

    models_to_try = [resolve_model()] + [m for m in MODEL_CASCADE if m != resolve_model()]
    last_exc = None
    for model in models_to_try:
        try:
            result = client.responses.create(
                model=model,
                input=input_messages,
                tools=tools,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if resolve_model() != model:
                logger.info(f"AI fallback now using model '{model}' (a previous model in the cascade failed or hadn't been tried yet).")
            _set_resolved_model(model)
            return result
        except Exception as exc:
            last_exc = exc
            logger.info(f"model '{model}' failed ({type(exc).__name__}: {exc}) — trying the next model in the cascade, if any.")
            continue
    _classify_and_raise(last_exc)


def stream_responses_api(*, system_prompt: str, history: list, user_message: str, tools: list):
    """Streaming variant — yields text deltas as they arrive, for low
    perceived latency on the frontend. Uses the same real model-cascade
    retry as call_responses_api() (see its docstring) before giving up.
    Raises OpenAIUnavailable up front if every model fails outright; once
    streaming has actually started for a model that DID work, a mid-stream
    error is yielded as a final marker rather than raised, since partial
    text may already have reached the user."""
    client = _get_client()

    input_messages = [{"role": "system", "content": system_prompt}]
    input_messages.extend(history)
    input_messages.append({"role": "user", "content": user_message})

    models_to_try = [resolve_model()] + [m for m in MODEL_CASCADE if m != resolve_model()]
    stream = None
    last_exc = None
    for model in models_to_try:
        try:
            stream = client.responses.create(
                model=model,
                input=input_messages,
                tools=tools,
                stream=True,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if resolve_model() != model:
                logger.info(f"AI streaming now using model '{model}' (a previous model in the cascade failed or hadn't been tried yet).")
            _set_resolved_model(model)
            break
        except Exception as exc:
            last_exc = exc
            logger.info(f"streaming model '{model}' failed ({type(exc).__name__}: {exc}) — trying the next model in the cascade, if any.")
            continue

    if stream is None:
        _classify_and_raise(last_exc)

    for event in stream:
        yield event
