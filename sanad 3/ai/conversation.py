#!/usr/bin/python3
"""Builds the input the AI fallback layer sends to OpenAI: system prompt
(with grounded account context) + recent history + the new user message."""

from . import prompt as prompt_mod
from . import memory


def build_call_inputs(session, message: str, account: dict, current_page: str, senior_mode: bool = False):
    account_context = prompt_mod.build_account_context(account)
    system_prompt = prompt_mod.build_system_prompt(account_context, current_page, senior_mode=senior_mode)
    history = memory.get_history(session)
    return system_prompt, history
