#!/usr/bin/python3
"""
Lightweight, dependency-free SEMANTIC intent matching for سَند.

Replaces the previous approach (difflib.get_close_matches — pure whole-
string character-overlap fuzzy matching against ~25 fixed command strings)
which is why a paraphrase like "أنا مسافر" (I'm traveling) or "أحتاج قرض"
(I need a loan) would get force-matched to whatever known command happened
to share the most characters, instead of correctly registering as "this
isn't any known navigation command".

Approach: each intent is represented by a broad set of hand-written
paraphrases (not just one canonical phrase). Input text and every
paraphrase are embedded as weighted character n-gram (2/3-gram) vectors,
and matched by cosine similarity — a real semantic-similarity technique
(the same bag-of-n-grams idea underlying classic subword/fastText-style
embeddings), just without requiring a multi-hundred-MB model download in
an environment that cannot fetch one at runtime. This generalizes far
better across paraphrasing/dialect variation than whole-string fuzzy
matching, while requiring zero extra dependencies.

If a real sentence-embedding model becomes available in the deployment
environment (e.g. `sentence-transformers` installed and its weights
already cached/downloaded by the user), `try_load_embedding_model()` below
will pick it up automatically and use it instead — a strict upgrade path,
never required.
"""

import math
import re
from difflib import SequenceMatcher

_DIACRITICS_RE = re.compile(r"[\u064B-\u065F\u0670\u0640]")
_PUNCT_RE = re.compile(r"[،,.؟!؛:\-_/\\\"'ـ]")
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _normalize(text: str) -> str:
    text = (text or "").translate(_AR_DIGITS)
    text = _DIACRITICS_RE.sub("", text)
    text = _PUNCT_RE.sub(" ", text)
    text = re.sub(r"[إأآ]", "ا", text)
    text = text.replace("ى", "ي")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


# ---------------------------------------------------------------------------
# Optional real embedding backend (used only if already installed/cached —
# never downloaded on demand, never required).
# ---------------------------------------------------------------------------
_st_model = None
_st_load_attempted = False


def _try_load_embedding_model():
    global _st_model, _st_load_attempted
    if _st_load_attempted:
        return _st_model
    _st_load_attempted = True
    try:
        from sentence_transformers import SentenceTransformer  # noqa
        _st_model = SentenceTransformer(
            "paraphrase-multilingual-MiniLM-L12-v2", local_files_only=True
        )
    except Exception:
        _st_model = None
    return _st_model


# ---------------------------------------------------------------------------
# Fallback: char n-gram bag-of-features cosine similarity
# ---------------------------------------------------------------------------
def _char_ngram_vector(text: str):
    norm = _normalize(text)
    padded = f" {norm} "
    counts = {}
    for n in (2, 3):
        for i in range(len(padded) - n + 1):
            key = (n, padded[i:i + n])
            counts[key] = counts.get(key, 0) + 1
    return counts


def _cosine(a: dict, b: dict) -> float:
    if not a or not b:
        return 0.0
    # Iterate the smaller dict for speed.
    if len(a) > len(b):
        a, b = b, a
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    if dot == 0:
        return 0.0
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


# ---------------------------------------------------------------------------
# Intent paraphrase bank — broad coverage per intent (Arabic + English,
# formal + Gulf dialect), so genuine navigation commands score high while
# unrelated remarks/questions ("أنا مسافر", "أحتاج قرض", "فقدت بطاقتي")
# score low against ALL of them and correctly fall through to the AI layer.
# ---------------------------------------------------------------------------
INTENT_EXAMPLES = {
    "balance": [
        "كم رصيدي", "كم الرصيد", "وش رصيدي", "كم معي فلوس", "اعرض الرصيد",
        "أبي أشوف رصيدي", "ابغى اعرف رصيدي", "وضح لي رصيد حسابي",
        "كم فلوسي في الحساب", "أريد معرفة رصيدي الحالي", "ورني كم عندي",
        "what's my balance", "how much money do i have", "show my balance",
        "check my account balance", "how much do i have in my account",
    ],
    "cards": [
        "افتح البطاقات", "بطاقاتي", "اعرض البطاقات", "ورني بطاقاتي",
        "أريد أشوف بطاقاتي", "افتح صفحة البطاقات", "وش بطاقاتي المتاحة",
        "show my cards", "open cards", "open my cards page", "view my cards",
    ],
    "transactions": [
        "كشف الحساب", "آخر العمليات", "اعرض آخر العمليات", "ورني العمليات",
        "أريد كشف حسابي", "وريني آخر التحويلات", "سجل العمليات",
        "show my transactions", "open transaction history", "show statement",
        "view my recent transactions",
    ],
    "home": [
        "الرئيسية", "ارجع للرئيسية", "افتح الصفحة الرئيسية", "روح للرئيسية",
        "رجعني للصفحة الرئيسية", "افتح لوحة التحكم",
        "go home", "open dashboard", "back to home page", "main page",
    ],
    "notifications": [
        "الإشعارات", "افتح الإشعارات", "ورني الإشعارات", "اعرض التنبيهات",
        "عندي إشعارات جديدة", "وش عندي تنبيهات",
        "show notifications", "open notifications", "any new alerts",
    ],
    "settings": [
        "الإعدادات", "افتح الإعدادات", "أريد أغير إعداداتي", "اعدادات الحساب",
        "افتح صفحة الإعدادات",
        "open settings", "go to settings", "account settings",
    ],
    "branch": [
        "وين أقرب فرع", "أريد أقرب فرع للبنك", "دلني على أقرب فرع",
        "فين أقرب فرع",
        "where's the nearest branch", "find nearest bank branch",
    ],
    "atm": [
        "وين أقرب صراف", "أريد أقرب صراف آلي", "دلني على أقرب ماكينة صراف",
        "فين أقرب ATM",
        "where's the nearest atm", "find nearest cash machine",
    ],
    "beneficiary": [
        "المستفيدين", "افتح المستفيدين", "اعرض المستفيدين", "ورني قائمة المستفيدين",
        "أريد أشوف المستفيدين المسجلين", "افتح صفحة المستفيدين",
        "show beneficiaries", "open beneficiaries page", "view my payees",
    ],
    "transfer": [
        "حول مبلغ", "أريد أحول فلوس", "ابغى احول لحد", "حول لي مبلغ لشخص",
        "افتح صفحة التحويل", "أبغى أسوي تحويل",
        "transfer money", "send money to someone", "open transfer page",
        "i want to make a transfer",
    ],
    "help": [
        "ساعدني", "وش تقدر تسوي", "ايش خدماتك", "أحتاج مساعدة",
        "what can you do", "help me", "what can you help with",
    ],
    "accounts": [
        "اسم صاحب الحساب", "معلومات حسابي", "بيانات الحساب",
        "my account info", "account holder name", "account details",
    ],
}

_EXAMPLE_VECTORS = None  # lazily built (module-level cache)


def _build_example_vectors():
    global _EXAMPLE_VECTORS
    if _EXAMPLE_VECTORS is not None:
        return _EXAMPLE_VECTORS
    _EXAMPLE_VECTORS = {
        intent: [(_char_ngram_vector(ex), ex) for ex in examples]
        for intent, examples in INTENT_EXAMPLES.items()
    }
    return _EXAMPLE_VECTORS


def best_semantic_intent(text: str):
    """Returns (intent, similarity 0.0-1.0, matched_example) — the best
    single paraphrase match across all intents. Uses a real embedding
    model if one happens to be available/cached; otherwise the
    dependency-free char-ngram cosine fallback."""
    if not (text or "").strip():
        return "unknown", 0.0, None

    model = _try_load_embedding_model()
    if model is not None:
        try:
            examples = INTENT_EXAMPLES
            flat = [(intent, ex) for intent, exs in examples.items() for ex in exs]
            texts = [text] + [ex for _, ex in flat]
            embeddings = model.encode(texts, normalize_embeddings=True)
            query_vec = embeddings[0]
            best_intent, best_score, best_ex = "unknown", 0.0, None
            for (intent, ex), vec in zip(flat, embeddings[1:]):
                score = float(sum(a * b for a, b in zip(query_vec, vec)))
                if score > best_score:
                    best_intent, best_score, best_ex = intent, score, ex
            return best_intent, best_score, best_ex
        except Exception:
            pass  # fall through to the dependency-free path below

    vectors = _build_example_vectors()
    query_vec = _char_ngram_vector(text)
    best_intent, best_score, best_ex = "unknown", 0.0, None
    for intent, examples in vectors.items():
        for ex_vec, ex_text in examples:
            score = _cosine(query_vec, ex_vec)
            if score > best_score:
                best_intent, best_score, best_ex = intent, score, ex_text
    # Small bonus if the raw strings are also literally very similar
    # (handles short exact/near-exact matches n-grams can under-score).
    if best_ex is not None:
        literal = SequenceMatcher(None, _normalize(text), _normalize(best_ex)).ratio()
        best_score = max(best_score, literal * 0.85)
    return best_intent, best_score, best_ex
