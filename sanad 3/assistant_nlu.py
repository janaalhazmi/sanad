#!/usr/bin/python3
"""
Bilingual (Arabic / English) NLU helpers for the سَند assistant.

Wraps the trained Arabic intent classifier (sand_model.pkl), adds an English
keyword classifier, spoken-number parsing (Arabic words, English words, and
digits), OTP digit-sequence parsing, beneficiary fuzzy matching, and voice
navigation-command detection.
"""

import re
import os
from difflib import get_close_matches, SequenceMatcher

import joblib

import assistant_semantic

MODEL_FILE = os.path.join(os.path.dirname(__file__), "sand_model.pkl")

_ar_model = None


def _get_model():
    """Lazily load (and cache) the trained intent classifier."""
    global _ar_model
    if _ar_model is None:
        _ar_model = joblib.load(MODEL_FILE)
    return _ar_model


def warm_up():
    """Load the model in a background thread so the first real request
    doesn't pay the load cost (kept out of the request/response path)."""
    try:
        _get_model()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------
ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_DIACRITICS_RE = re.compile(r"[\u064B-\u065F\u0670\u0640]")  # harakat + tatweel
_PUNCT_RE = re.compile(r"[،,.؟!؛:\-_/\\\"'ـ]")

FILLER_WORDS = {
    "يعني", "اه", "أه", "امم", "ايه", "اوه", "طيب", "خلاص", "المهم",
    "um", "uh", "hmm", "like", "well", "so", "the", "a", "an",
}


def is_arabic(text: str) -> bool:
    return bool(ARABIC_RE.search(text or ""))


def _normalize_digits(text: str) -> str:
    return (text or "").translate(_AR_DIGITS)


def light_normalize(text: str) -> str:
    """Safe normalization used before ML intent classification: digit
    conversion + diacritics/punctuation stripping + whitespace collapse.
    Does NOT unify alef forms, so it won't shift the trained vocabulary."""
    text = _normalize_digits(text or "")
    text = _DIACRITICS_RE.sub("", text)
    text = _PUNCT_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_arabic(text: str) -> str:
    """Aggressive normalization for keyword/number matching: light_normalize
    plus alef/ya unification. Used only where our own dictionaries are
    written in the unified spelling (number words, trigger phrases)."""
    text = light_normalize(text)
    text = re.sub(r"[إأآ]", "ا", text)
    text = text.replace("ى", "ي")
    return text


def strip_fillers(text: str) -> str:
    tokens = [t for t in text.split(" ") if t and t.lower() not in FILLER_WORDS]
    return " ".join(tokens)


# ---------------------------------------------------------------------------
# NOTE: this fixed list is kept only as a harmless legacy reference — intent
# classification confidence no longer uses get_close_matches against it (see
# detect_intent_with_confidence(), which now uses assistant_semantic.py's
# broader paraphrase-bank + cosine-similarity matching instead). detect_intent()
# below (the plain, non-confidence variant) still uses it for backward
# compatibility with any existing caller.
AR_COMMANDS = [
    "كم رصيدي", "كم الرصيد", "وش رصيدي", "كم معي", "اعرض الرصيد", "أبي أشوف رصيدي",
    "افتح البطاقات", "بطاقاتي", "اعرض البطاقات", "ورني بطاقاتي",
    "كشف الحساب", "آخر العمليات", "اعرض آخر العمليات", "ورني العمليات",
    "الرئيسية", "ارجع للرئيسية", "افتح الصفحة الرئيسية",
    "الإعدادات", "الإشعارات",
    "وين أقرب صراف", "وين أقرب فرع",
    "حول مبلغ", "ساعدني",
    "المستفيدين", "افتح المستفيدين",
]

EN_KEYWORDS = {
    "balance": ["balance", "how much", "my money", "funds"],
    "cards": ["card", "cards"],
    "transactions": ["transaction", "transactions", "statement", "history"],
    "home": ["home", "main page", "dashboard"],
    "notifications": ["notification", "notifications", "alerts"],
    "settings": ["settings", "setting", "profile", "password"],
    "branch": ["branch", "nearest branch"],
    "atm": ["atm", "cash machine", "cashpoint"],
    "beneficiary": ["beneficiary", "beneficiaries", "payee", "recipient", "contacts"],
    "transfer": ["transfer", "send money", "pay", "send "],
    "help": ["help", "what can you do", "assist"],
    "accounts": ["account", "accounts"],
}


def detect_intent(text: str) -> str:
    """Detect an intent from Arabic or English free text."""
    raw = (text or "").strip()
    if not raw:
        return "unknown"

    cleaned = light_normalize(raw)

    if is_arabic(cleaned):
        match = get_close_matches(cleaned, AR_COMMANDS, n=1, cutoff=0.35)
        corrected = match[0] if match else cleaned
        try:
            model = _get_model()
            return model.predict([corrected])[0]
        except Exception:
            return "unknown"

    lowered = cleaned.lower()
    for intent, keywords in EN_KEYWORDS.items():
        for kw in keywords:
            if kw in lowered:
                return intent
    return "unknown"


# Strong signal words for "this is an open-ended question or opinion/
# recommendation request", never present in any trained command (verified
# empirically against commands.csv — zero occurrences), so this check can
# only ever LOWER a confidence that would otherwise be wrong; it can never
# suppress a legitimate simple command like "لو سمحت كم رصيدي من فضلك".
#
# Why this exists: once text gets fuzzy-corrected to a short known command
# string (e.g. "ما رسوم السحب النقدي من الخارج" -> "اعرض البطاقات"), the
# classifier is then run on the CORRECTED string and is tautologically
# confident about it — the real signal that mattered (how loosely the
# original text actually resembled that command) already got baked in as
# only a middling `similarity` score, but a message like this can still
# clear the 0.35 confidence threshold. This guard catches exactly that gap.
_OPEN_QUESTION_MARKERS = (
    "رسوم", "قرض", "قروض", "تمويل", "تمويلي", "أفضل", "افضل", "الفرق", "رأيك", "رايك",
    "توصية", "سفر", "نصيحة", "ليش", "ايش رايك", "وش رايك",
    "أطلع", "اطلع", "احصل", "استخرج", "قسط", "اقساط", "مرابحة", "تقسيط",
    "فتح حساب", "بطاقة ائتمان", "كيف اقدر", "كيف يمكن", "شروط", "أهلية",
)

# Broader structural signal, on top of the topic-keyword list above: no
# local navigation/single-turn command in this app is EVER phrased as a
# "كيف/ليش/لماذا/متى ...؟" question (verified against AR_COMMANDS,
# assistant_semantic.INTENT_EXAMPLES, and NAV_COMMANDS below — none start
# this way), so any message that DOES start with one of these question
# words is always treated as open-ended and routed to the AI layer,
# regardless of which topic words it happens to contain. This is what
# catches phrasings the topic-keyword list above doesn't happen to name,
# e.g. "كيف أرفع الحد الائتماني؟" (no "قرض"/"تمويل"/etc, but still clearly
# a how-to question, not a command). Deliberately excludes "ما/هل" prefixes
# — those DO legitimately open real local commands (e.g. "ما هو رصيدي؟"),
# so blanket-catching them would break the fast local balance/cards/etc.
# replies instead of only fixing the AI-routing gap.
_GENERIC_QUESTION_PREFIX_RE = re.compile(r"^\s*(?:كيف|ليش|ليه|لماذا|متى|وش سبب|ليش سبب)\b")


# ---------------------------------------------------------------------------
# Emergency / safety phrases — ALWAYS the highest-priority check in the
# whole assistant pipeline (see app_server.py's _resolve_locally(), where
# this is checked before anything else: before login gating, before any
# active flow, before local-intent detection, before the AI fallback).
# A user reporting fraud/theft/threat/account compromise must get an
# immediate, unambiguous safety response and be moved to safety — never a
# clarifying question, never normal NLU/AI routing, and never left mid-way
# through an unrelated flow (which gets dropped immediately, since the
# account may be compromised).
# ---------------------------------------------------------------------------
_EMERGENCY_TRIGGER_RE = re.compile(
    r"مهدد|بخطر|في خطر|خطر يهددني|"
    r"احتيال|للاحتيال|النصب|نصبوا علي|"
    r"سرق|سرقو|سرقوا|انسرق|سرقت|"
    r"اختراق|مخترق|اخترقوا|اخترق حسابي|"
    r"threat|danger|fraud|scam|stolen|robbed|hacked|compromised",
    re.IGNORECASE,
)


def is_emergency(text: str) -> bool:
    """True for any phrase describing an active security emergency
    (threat, fraud, theft, a hacked/compromised account) — in Arabic or
    English, formal or dialectal."""
    raw = (text or "").strip()
    if not raw:
        return False
    return bool(_EMERGENCY_TRIGGER_RE.search(normalize_arabic(raw))) or bool(
        _EMERGENCY_TRIGGER_RE.search(raw.lower())
    )


def detect_intent_with_confidence(text: str):
    """Like detect_intent(), but also returns a 0.0-1.0 confidence score.

    Uses semantic (character-embedding cosine similarity) matching against
    a broad paraphrase bank per intent — see assistant_semantic.py — instead
    of whole-string fuzzy matching against a handful of fixed command
    strings. This is why a paraphrase like "أنا مسافر" (I'm traveling) or
    "أحتاج قرض" (I need a loan) now correctly scores LOW similarity against
    every known navigation intent (they simply aren't paraphrases of any of
    them) and falls through to the AI layer, instead of being force-matched
    to whichever known command happens to share the most characters.

    Why this exists at all: the trained classifier only knows ~12 intents
    and, being a forced-choice classifier, ALWAYS predicts one of them — it
    has no built-in way to say "none of these fit". This function adds that
    missing signal on top, without changing detect_intent()'s existing
    behavior for any caller that already depends on it.
    """
    raw = (text or "").strip()
    if not raw:
        return "unknown", 0.0

    cleaned = light_normalize(raw)

    if any(marker in cleaned for marker in _OPEN_QUESTION_MARKERS) or _GENERIC_QUESTION_PREFIX_RE.match(cleaned):
        try:
            intent = _get_model().predict([cleaned])[0]
        except Exception:
            intent = "unknown"
        return intent, 0.1

    if is_arabic(cleaned):
        semantic_intent, semantic_score, _matched = assistant_semantic.best_semantic_intent(cleaned)

        if semantic_score < 0.30:
            # Nothing in our paraphrase bank even loosely resembles this
            # text — low confidence regardless of what the model forces
            # itself to predict below.
            try:
                intent = _get_model().predict([cleaned])[0]
            except Exception:
                intent = "unknown"
            return intent, min(semantic_score, 0.2)

        try:
            model = _get_model()
            proba = model.predict_proba([cleaned])[0]
            model_confidence = float(max(proba))
            model_intent = model.classes_[int(proba.argmax())]
        except Exception:
            model_confidence = 0.5
            model_intent = semantic_intent

        # Conservative ensemble: trust the semantic match's intent label
        # (broader paraphrase coverage) when the trained classifier agrees
        # it's at least plausible, or when the semantic similarity is very
        # strong on its own; otherwise defer to the classifier's own guess
        # but at a discounted confidence, since the two signals disagree.
        if semantic_intent == model_intent or semantic_score >= 0.55:
            return semantic_intent, min(semantic_score, model_confidence)
        return model_intent, model_confidence * 0.6

    lowered = cleaned.lower()
    for intent, keywords in EN_KEYWORDS.items():
        for kw in keywords:
            if kw in lowered:
                return intent, 0.9
    return "unknown", 0.0


# ---------------------------------------------------------------------------
# Trigger detection (transfer / add-beneficiary) — keyword fast-path that
# backs up the small ML classifier, which gets confused by numbers/names.
# ---------------------------------------------------------------------------
_TRANSFER_TRIGGER_RE = re.compile(
    r"حول|احول|ابغي احول|ابي احول|ارسل|ارسال|transfer|send\s+money|send\s+\d|send\s+[a-z]+\s+to",
    re.IGNORECASE,
)


def looks_like_transfer(text: str) -> bool:
    return bool(_TRANSFER_TRIGGER_RE.search(normalize_arabic(text)))


_ADD_BENEFICIARY_TRIGGER_RE = re.compile(
    r"اضف مستفيد|اضافة مستفيد|ضيف مستفيد|مستفيد جديد|"
    r"add\s+(a\s+)?(new\s+)?benefi|new\s+benefi",
    re.IGNORECASE,
)


def looks_like_add_beneficiary(text: str) -> bool:
    return bool(_ADD_BENEFICIARY_TRIGGER_RE.search(normalize_arabic(text)))


# ---------------------------------------------------------------------------
# Spoken-number parsing (Arabic words, English words, digits)
# ---------------------------------------------------------------------------
_AR_UNITS = {
    "صفر": 0,
    "واحد": 1, "واحده": 1, "واحدة": 1,
    "اثنان": 2, "اثنين": 2, "ثنين": 2,
    "ثلاثة": 3, "ثلاثه": 3, "ثلاث": 3,
    "اربعة": 4, "اربعه": 4, "اربع": 4,
    "خمسة": 5, "خمسه": 5, "خمس": 5,
    "ستة": 6, "سته": 6, "ست": 6,
    "سبعة": 7, "سبعه": 7, "سبع": 7,
    "ثمانية": 8, "ثمانيه": 8, "ثماني": 8,
    "تسعة": 9, "تسعه": 9, "تسع": 9,
}
_AR_TENS = {
    "عشرة": 10, "عشره": 10, "عشر": 10,
    "عشرون": 20, "عشرين": 20,
    "ثلاثون": 30, "ثلاثين": 30,
    "اربعون": 40, "اربعين": 40,
    "خمسون": 50, "خمسين": 50,
    "ستون": 60, "ستين": 60,
    "سبعون": 70, "سبعين": 70,
    "ثمانون": 80, "ثمانين": 80,
    "تسعون": 90, "تسعين": 90,
}
_AR_HUNDREDS = {
    "مئة": 100, "مئه": 100, "مائة": 100, "مائه": 100,
    "مئتان": 200, "مئتين": 200, "مائتان": 200, "مائتين": 200,
    "ثلاثمئة": 300, "ثلاثمائة": 300,
    "اربعمئة": 400, "اربعمائة": 400,
    "خمسمئة": 500, "خمسمائة": 500,
    "ستمئة": 600, "ستمائة": 600,
    "سبعمئة": 700, "سبعمائة": 700,
    "ثمانمئة": 800, "ثمانمائة": 800,
    "تسعمئة": 900, "تسعمائة": 900,
}
_AR_THOUSAND_WORDS = {"الف", "آلاف", "الاف", "الآف"}
_AR_HUNDRED_WORDS = {"مئة", "مئه", "مائة", "مائه"}
_AR_NUMBER_WORDS = {**_AR_UNITS, **_AR_TENS, **_AR_HUNDREDS}
_AR_CONNECTORS = {"و", "او", "أو"}


def words_to_number_ar(text: str):
    """Parse a compound Arabic number phrase, e.g. 'مئتين وخمسين' -> 250,
    'ثلاثة الاف' -> 3000, 'عشرين' -> 20."""
    norm = normalize_arabic(text)
    tokens = [t for t in norm.split(" ") if t and t not in _AR_CONNECTORS]
    if not tokens:
        return None

    total = 0
    found = False
    i, n = 0, len(tokens)
    while i < n:
        tok = tokens[i]
        # Arabic "and" is often glued to the next word with no space
        if tok not in _AR_UNITS and tok not in _AR_TENS and tok not in _AR_HUNDREDS \
                and tok not in _AR_THOUSAND_WORDS and tok.startswith("و") and len(tok) > 1:
            stripped = tok[1:]
            if stripped in _AR_UNITS or stripped in _AR_TENS or stripped in _AR_HUNDREDS or stripped in _AR_THOUSAND_WORDS:
                tok = stripped
        nxt = tokens[i + 1] if i + 1 < n else None
        if tok in _AR_UNITS and nxt in _AR_THOUSAND_WORDS:
            total += _AR_UNITS[tok] * 1000
            found = True
            i += 2
            continue
        if tok in _AR_UNITS and nxt in _AR_HUNDRED_WORDS:
            total += _AR_UNITS[tok] * 100
            found = True
            i += 2
            continue
        if tok in _AR_THOUSAND_WORDS:
            total += 1000
            found = True
            i += 1
            continue
        if tok in _AR_NUMBER_WORDS:
            total += _AR_NUMBER_WORDS[tok]
            found = True
            i += 1
            continue
        i += 1
    return total if found else None


_EN_UNITS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}
_EN_TEENS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_EN_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}


def words_to_number_en(text: str):
    tokens = re.sub(r"[^a-zA-Z\s]", " ", (text or "").lower()).split()
    total, current, found = 0, 0, False
    for tok in tokens:
        if tok in _EN_UNITS:
            current += _EN_UNITS[tok]
            found = True
        elif tok in _EN_TEENS:
            current += _EN_TEENS[tok]
            found = True
        elif tok in _EN_TENS:
            current += _EN_TENS[tok]
            found = True
        elif tok == "hundred":
            current = (current or 1) * 100
            found = True
        elif tok == "thousand":
            total += (current or 1) * 1000
            current = 0
            found = True
    total += current
    return total if found else None


def parse_amount(text: str):
    """Best-effort amount parser: digits first, then Arabic words, then
    English words. Returns a float or None."""
    if not text:
        return None
    digit_match = re.search(r"\d+(?:\.\d+)?", _normalize_digits(text))
    if digit_match:
        return float(digit_match.group())
    val = words_to_number_ar(text)
    if val is not None:
        return float(val)
    val = words_to_number_en(text)
    if val is not None:
        return float(val)
    return None


# ---------------------------------------------------------------------------
# OTP digit-sequence parsing ("واحد اثنين ثلاثة..." -> "123", "one two" -> "12")
# ---------------------------------------------------------------------------
_AR_DIGIT_WORD = {
    "صفر": "0", "واحد": "1", "واحده": "1", "واحدة": "1",
    "اثنان": "2", "اثنين": "2", "ثنين": "2",
    "ثلاثة": "3", "ثلاثه": "3", "ثلاث": "3",
    "اربعة": "4", "اربعه": "4", "اربع": "4",
    "خمسة": "5", "خمسه": "5", "خمس": "5",
    "ستة": "6", "سته": "6", "ست": "6",
    "سبعة": "7", "سبعه": "7", "سبع": "7",
    "ثمانية": "8", "ثمانيه": "8", "ثماني": "8",
    "تسعة": "9", "تسعه": "9", "تسع": "9",
}
_EN_DIGIT_WORD = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def words_to_digit_string(text: str):
    """Convert a spoken digit-by-digit sequence to a plain digit string.
    'واحد اثنين ثلاثة أربعة خمسة ستة' -> '123456'. Falls back to any bare
    digits already present in the text (Arabic-Indic or ASCII)."""
    if not text:
        return None

    norm = normalize_arabic(text)
    tokens = [t for t in norm.split(" ") if t]
    ar_digits = [_AR_DIGIT_WORD[t] for t in tokens if t in _AR_DIGIT_WORD]
    if ar_digits:
        return "".join(ar_digits)

    lowered_tokens = re.sub(r"[^a-zA-Z\s]", " ", (text or "").lower()).split()
    en_digits = [_EN_DIGIT_WORD[t] for t in lowered_tokens if t in _EN_DIGIT_WORD]
    if en_digits:
        return "".join(en_digits)

    bare_digits = re.sub(r"\D", "", _normalize_digits(text))
    return bare_digits or None


# ---------------------------------------------------------------------------
# Transfer extraction: amount + beneficiary name from a free-form sentence
# ---------------------------------------------------------------------------
EN_TRANSFER_RE = re.compile(
    r"(?:transfer|send)\s+(?:sar\s*)?(\d+(?:\.\d+)?)\s*(?:sar|riyal|riyals)?\s*(?:to)?\s*(.+)",
    re.IGNORECASE,
)
EN_TRANSFER_WORD_RE = re.compile(
    r"(?:transfer|send)\s+(.+?)\s+to\s+(.+)$",
    re.IGNORECASE,
)
AR_TRANSFER_CONNECTOR_RE = re.compile(
    r"(?:حول|ابغي احول|ابي احول|ارسل|ارسال)\s+(.+?)\s+(?:الي|الى|لـ)\s+(.+)$"
)
# Arabic prepositions often glue directly onto the following word with no
# space ("لأحمد" = "to Ahmed"), so this variant matches a standalone 'ل'
# only when it's preceded by whitespace and immediately followed by a
# non-space character (the glued name) — never a 'ل' that just happens to
# appear inside another word like "ريال" or "الف".
AR_TRANSFER_GLUED_RE = re.compile(
    r"(?:حول|ابغي احول|ابي احول|ارسل|ارسال)\s+(.+?)\s+ل(\S.*)$"
)
AR_TRANSFER_NO_NAME_RE = re.compile(
    r"(?:حول|ابغي احول|ابي احول|ارسل|ارسال)\s+(.+?)\s*ريال"
)


def extract_transfer(text: str):
    """Try to pull (amount, beneficiary_name) out of a sentence in AR or EN,
    understanding digits, Arabic number words, and English number words."""
    raw = (text or "").strip()
    if not raw:
        return None, None

    norm = normalize_arabic(strip_fillers(raw))

    m = AR_TRANSFER_CONNECTOR_RE.search(norm)
    if m:
        amount_phrase = re.sub(r"ريال.*$", "", m.group(1)).strip()
        name = m.group(2).strip(" .؟!،,")
        amount = parse_amount(amount_phrase)
        if amount:
            return amount, (name or None)

    m = AR_TRANSFER_GLUED_RE.search(norm)
    if m:
        amount_phrase = re.sub(r"ريال.*$", "", m.group(1)).strip()
        name = m.group(2).strip(" .؟!،,")
        amount = parse_amount(amount_phrase)
        if amount:
            return amount, (name or None)

    m = AR_TRANSFER_NO_NAME_RE.search(norm)
    if m:
        amount = parse_amount(m.group(1))
        if amount:
            return amount, None

    m = EN_TRANSFER_RE.search(raw)
    if m:
        try:
            amount = float(m.group(1))
        except ValueError:
            amount = None
        name = m.group(2).strip(" .?!,")
        if amount:
            return amount, name or None

    m = EN_TRANSFER_WORD_RE.search(raw)
    if m:
        amount = parse_amount(m.group(1))
        name = m.group(2).strip(" .?!,")
        if amount:
            return amount, name or None

    # Last resort: any recognizable number anywhere in the message
    amount = parse_amount(norm) or parse_amount(raw)
    return (amount, None) if amount else (None, None)


# ---------------------------------------------------------------------------
# Yes/no
# ---------------------------------------------------------------------------
AFFIRM_WORDS = {
    "نعم", "اي", "ايه", "إيه", "أكيد", "اكيد", "طبعاً", "طبعا", "تمام", "أوكي", "اوك", "ايوه",
    "yes", "yep", "sure", "ok", "okay", "confirm", "done",
}
NEGATIVE_WORDS = {
    "لا", "لا شكرا", "لا شكراً", "إلغاء", "الغاء", "كنسل", "ابغى الغاء",
    "no", "nope", "cancel", "stop",
}


def is_affirmative(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in AFFIRM_WORDS or any(w in t for w in AFFIRM_WORDS if len(w) > 2)


def is_negative(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in NEGATIVE_WORDS or any(w in t for w in NEGATIVE_WORDS if len(w) > 2)


# ---------------------------------------------------------------------------
# Auth method choice ("fingerprint" vs "OTP/SMS")
# ---------------------------------------------------------------------------
FINGERPRINT_WORDS = {"بصمة", "بصمه", "fingerprint", "face id", "faceid", "بصمتي"}
OTP_WORDS = {"رمز", "otp", "sms", "رسالة", "رساله", "رمز التحقق", "sms verification", "text message"}


def detect_auth_method(text: str):
    t = normalize_arabic(text or "")
    if any(w in t for w in [normalize_arabic(w) for w in FINGERPRINT_WORDS]):
        return "webauthn"
    if any(w in t for w in [normalize_arabic(w) for w in OTP_WORDS]):
        return "otp"
    return None


# ---------------------------------------------------------------------------
# Voice navigation commands
# ---------------------------------------------------------------------------
NAV_COMMANDS = [
    # (trigger regex, route name or special action)
    (re.compile(r"الرئيسية|افتح الصفحة الرئيسية|dashboard|home\b", re.IGNORECASE), "dashboard"),
    (re.compile(r"البطاقات|بطاقاتي|open cards|cards\b", re.IGNORECASE), "cards"),
    (re.compile(r"التحويل|صفحة التحويل|open transfer|transfer page", re.IGNORECASE), "transfer"),
    (re.compile(r"المستفيدين|صفحة المستفيدين|open beneficiaries|beneficiaries page", re.IGNORECASE), "beneficiaries-page"),
    (re.compile(r"الإشعارات|open notifications|notifications page", re.IGNORECASE), "notifications-page"),
    (re.compile(r"كشف الحساب|آخر العمليات|open transactions|transactions page", re.IGNORECASE), "transactions-page"),
    (re.compile(r"الإعدادات|open settings|settings page", re.IGNORECASE), "settings"),
    (
        re.compile(
            r"المساعد الصوتي|المساعد\b|افتح المساعد|اذهب.*المساعد|روح.*المساعد|"
            r"open assistant|go to assistant|voice assistant",
            re.IGNORECASE,
        ),
        "assistant",
    ),
]


def detect_navigation(text: str):
    """Return a route slug like 'dashboard' for explicit page-open commands,
    or a special token ('back', 'logout', 'close', 'cancel') for the rest."""
    norm = normalize_arabic(text or "")
    low = (text or "").lower()

    if re.search(r"ارجع|رجوع|go back|back\b", norm) or "go back" in low or low.strip() == "back":
        return "back"
    if re.search(r"تسجيل الخروج|اخرج|سجل خروج|logout|log out|sign out", norm) or "logout" in low:
        return "logout"
    if re.search(r"اغلاق|اقفل|close\b", norm) or "close" in low:
        return "close"
    if re.search(r"الغاء|إلغاء|cancel\b", norm) or "cancel" in low:
        return "cancel"
    if re.search(r"اقرا الشاشة|اقرأ الشاشة|read the screen|read screen", norm) or "read the screen" in low or "read screen" in low:
        return "read_screen"

    for pattern, target in NAV_COMMANDS:
        if pattern.search(norm) or pattern.search(text or ""):
            return target
    return None


# ---------------------------------------------------------------------------
# Beneficiary matching
# ---------------------------------------------------------------------------
TRANSLITERATION_ALIASES = {
    "ahmed": "أحمد", "ahmad": "أحمد", "ahmet": "أحمد",
    "mohammed": "محمد", "muhammad": "محمد", "mohammad": "محمد", "mohamed": "محمد",
}


def _match_exact(beneficiaries, name):
    normalized_target = normalize_arabic(name)
    for b in beneficiaries:
        if normalized_target == normalize_arabic(b["name"]) or normalized_target == normalize_arabic(b.get("nickname", "")):
            return b
    return None


def find_beneficiary(beneficiaries, name_hint):
    """Fuzzy-match a spoken/typed name against the beneficiaries list."""
    if not name_hint:
        return None

    name_hint = name_hint.strip()

    alias = TRANSLITERATION_ALIASES.get(name_hint.lower())
    if alias:
        hit = _match_exact(beneficiaries, alias)
        if hit:
            return hit

    # Alef/hamza-normalized exact match first — this correctly resolves
    # "احمد" to "أحمد" without falling through to fuzzy matching, which can
    # otherwise pick a *different*, equally-similar-looking name by accident.
    hit = _match_exact(beneficiaries, name_hint)
    if hit:
        return hit

    # Arabic prepositions ("لأحمد" = "to Ahmed") are often glued to the name
    # with no space, which speech recognition preserves as one token.
    if name_hint.startswith("ل") and len(name_hint) > 1:
        hit = _match_exact(beneficiaries, name_hint[1:])
        if hit:
            return hit

    normalized_hint = normalize_arabic(name_hint)
    normalized_names = {}
    for b in beneficiaries:
        normalized_names[normalize_arabic(b["name"])] = b
        if b.get("nickname"):
            normalized_names[normalize_arabic(b["nickname"])] = b

    match = get_close_matches(normalized_hint, list(normalized_names.keys()), n=1, cutoff=0.6)
    if match:
        return normalized_names[match[0]]

    for b in beneficiaries:
        if normalized_hint in normalize_arabic(b["name"]) or normalized_hint in normalize_arabic(b.get("nickname", "")):
            return b

    return None
