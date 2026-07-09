#!/usr/bin/python3
"""
Tool (function) schemas exposed to the OpenAI Responses API.

IMPORTANT: these tools are declarative only. Calling one just means the
model is PROPOSING that action with some extracted arguments. Nothing here
executes anything — ai_engine.py hands the proposal back to app_server.py,
which routes it through the exact same _ask_method_or_gate() -> pending_action
-> OTP/WebAuthn -> _execute_pending_action() gate that the local NLU flow
already uses. The model has no path to move money or add a beneficiary
on its own.

Keep the `navigate` target enum in sync with NAV_TARGETS in app_server.py.
"""

NAV_TARGETS = [
    "dashboard", "cards", "transfer", "beneficiaries-page",
    "notifications-page", "transactions-page", "settings", "assistant",
]

TOOLS = [
    {
        "type": "function",
        "name": "propose_transfer",
        "description": (
            "اقترح تحويل مبلغ مالي إلى مستفيد. لا يُنفذ التحويل فعلياً — "
            "فقط يُرسل الاقتراح للمستخدم للتأكيد ثم التحقق الأمني. استخدمه "
            "فقط عندما يذكر المستخدم بوضوح مبلغاً واسم مستفيد (أو ما يكفي "
            "من السياق لتحديدهما)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "beneficiary_name": {
                    "type": "string",
                    "description": "اسم المستفيد كما ذكره المستخدم (بالعربية أو الإنجليزية)",
                },
                "amount": {
                    "type": "number",
                    "description": "المبلغ بالريال السعودي",
                },
            },
            "required": ["beneficiary_name", "amount"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "propose_add_beneficiary",
        "description": (
            "اقترح إضافة مستفيد جديد. لا يُنفذ الإضافة فعلياً — فقط يُرسل "
            "الاقتراح للمستخدم للتأكيد ثم التحقق الأمني. استخدمه فقط عندما "
            "يوفر المستخدم اسماً ورقم آيبان بوضوح."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "اسم المستفيد الجديد"},
                "iban": {"type": "string", "description": "رقم الآيبان"},
                "nickname": {
                    "type": "string",
                    "description": "اسم مختصر اختياري، أو نفس الاسم إن لم يُذكر",
                },
            },
            "required": ["name", "iban", "nickname"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "navigate",
        "description": "افتح صفحة معينة في التطبيق بناءً على طلب المستخدم الصريح للتنقل.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "enum": NAV_TARGETS},
            },
            "required": ["target"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]
