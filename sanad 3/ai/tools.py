#!/usr/bin/python3
"""
Generic (non-banking-specific) helpers for turning a raw Responses API
result into one normalized shape the rest of the app can use:

    {"type": "tool", "tool": "propose_transfer", "args": {...}}
    {"type": "tool", "tool": "propose_add_beneficiary", "args": {...}}
    {"type": "tool", "tool": "navigate", "args": {"target": "cards"}}
    {"type": "text", "text": "..."}

This intentionally does NOT execute anything — it just parses. Execution
routing lives in app_server.py, reusing the existing security-gated flows.
"""

import json


def parse_response(response) -> dict:
    """Normalize an OpenAI Responses API result object."""
    output = getattr(response, "output", None) or []

    for item in output:
        item_type = getattr(item, "type", None)
        if item_type == "function_call":
            name = getattr(item, "name", None)
            raw_args = getattr(item, "arguments", "{}") or "{}"
            try:
                args = json.loads(raw_args)
            except (ValueError, TypeError):
                args = {}
            return {"type": "tool", "tool": name, "args": args}

    # No tool call -> fall back to the aggregated text output.
    text = getattr(response, "output_text", None)
    if not text:
        # Defensive extraction if output_text helper isn't populated.
        parts = []
        for item in output:
            if getattr(item, "type", None) == "message":
                for block in getattr(item, "content", []) or []:
                    if getattr(block, "type", None) == "output_text":
                        parts.append(getattr(block, "text", ""))
        text = "".join(parts)

    return {"type": "text", "text": (text or "").strip()}


def parse_stream_event(event) -> dict | None:
    """Normalize one streaming event into either a text delta or a final
    tool-call, or None if the event carries nothing user-facing."""
    event_type = getattr(event, "type", "")
    if event_type == "response.output_text.delta":
        return {"kind": "delta", "text": getattr(event, "delta", "")}
    if event_type == "response.completed":
        return {"kind": "done", "response": getattr(event, "response", None)}
    return None
