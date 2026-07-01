# سَند (Sanad) — Digital Banking Demo

## How to run
```bash
pip install flask flask_cors joblib scikit-learn pandas cryptography --break-system-packages
python3 app_server.py
```
Open **http://127.0.0.1:5001** and log in with the demo account:
- **PIN:** `12345`
- **Phone (only needed for the classic login link):** `0500000000`

WebAuthn (biometric/Face ID) requires a "secure context" — `localhost`/`127.0.0.1`
qualifies automatically. Enable it from **Settings → الأمان** once logged in.

## This pass: critical bug fixes

### 1. Biometric authentication was FAKE — now real, everywhere it matters
The previous `/api/verify-biometric` endpoint **always returned success** with no
check at all. It has been **deleted**. There is now exactly one way to authenticate
a sensitive action with biometrics: `/api/action/webauthn/options` +
`/api/action/webauthn/verify`, which performs a **real cryptographic signature
check** (ES256/RS256 via the `cryptography` package) against the credential's
public key, the request's origin, RP-ID hash, and challenge freshness — see
`_verify_webauthn_assertion()` in `app_server.py`. If it isn't a real, valid,
fresh signature from the enrolled device, the action does not execute. Period.

If the browser/device doesn't support a platform authenticator (or the account
hasn't enrolled one), the `/auth-verify` page **automatically shows the SMS OTP
screen** instead — no dead end, no fake fallback.

### 2. Beneficiary creation had ZERO authentication — now gated
The old `/api/beneficiaries/add` endpoint saved a new beneficiary immediately,
with no check whatsoever. It has been **deleted**. Beneficiary creation now goes
through the exact same `/api/action/*` gate as a transfer (see below) — nothing
is saved until fingerprint or OTP verification actually succeeds.

### 3. One mandatory authentication gate for every sensitive action
`app_server.py` now has a small state machine: `/api/action/create` validates the
request (beneficiary must exist, amount must be ≤ balance, beneficiary name must
not already exist, etc.) and stores it as a **pending action** in the session —
nothing happens yet. The `/auth-verify` page (`templates/auth_verify.html`) then
offers **fingerprint or SMS OTP**; only a successful verification calls
`_execute_pending_action()`, which is the *only* code path that can move money or
save a beneficiary. This is used identically by:
- the **Transfer** page,
- the **Beneficiaries** page (add),
- the **voice/chat assistant** (transfer and "أضف مستفيد" flows), which now asks
  "fingerprint or OTP?" mid-conversation and routes to the same `/auth-verify`
  page, then completes the action automatically after verification succeeds.

### 4. Voice number recognition (Arabic words, English words, digits)
`assistant_nlu.py` now has a real Arabic number-word parser (`words_to_number_ar`)
and English one (`words_to_number_en`) covering units/tens/hundreds/thousands —
`"عشرين"→20`, `"خمسين"→50`, `"مئة"→100`, `"مئتين"→200`, `"ألف"→1000`,
`"مئتين وخمسين"→250`, `"fifty"→50`, plus digit normalization (`"٢٠"`, `"20"`).
Two real bugs were caught and fixed while building this:
- The old transfer-parsing regex let a bare "ل" connector match stray `ل`
  characters *inside* words like "ريال" or "الف", silently corrupting the parsed
  amount. Fixed with a properly anchored two-pattern approach (tested).
- Beneficiary name matching picked the *wrong* person ("محمد" instead of "أحمد")
  for hamza-normalized input, because fuzzy string matching on un-normalized
  Arabic text can tie on edit distance. Fixed by normalizing alef/hamza forms
  before comparing (tested with a synthetic beneficiary list).

### 5. OTP: auto-focus, paste, spoken digits, auto-verify
`auth_verify.html`'s OTP boxes: auto-focus on first box, `paste` event support
(splits a pasted 6-digit code — Arabic-Indic or ASCII — across the boxes),
a mic button for spoken digits ("واحد اثنين ثلاثة أربعة خمسة ستة" → `123456`,
converted server-side by `words_to_digit_string()`), and auto-verification the
moment all 6 boxes are filled. Verified end-to-end with a test that spoke a
random OTP code as Arabic words and confirmed it matched.

### 6. Assistant available on every page + "Read Screen"
Every page (via `templates/base.html`) now has a small floating widget
(`static/js/global_assistant.js`): a mic button and a "read screen" button.
"Read screen" walks the **live DOM** (headings, buttons, labels, input values,
list items, balance, etc.) — nothing is hardcoded per page — and reads it aloud
with `speechSynthesis`. The mic sends recognized speech to `/api/assistant` with
the current page name as context.

### 7. Voice navigation
The assistant now understands: "اذهب للرئيسية"/"go to dashboard", "افتح
البطاقات"/"open cards", "افتح التحويل", "افتح المستفيدين", "افتح الإشعارات",
"كشف الحساب", "الإعدادات", "ارجع"/"go back", "إلغاء"/"cancel", "اغلاق"/"close",
"تسجيل الخروج"/"logout", and "اقرأ الشاشة"/"read the screen" — see
`detect_navigation()` in `assistant_nlu.py`. These can interrupt a flow that's
only waiting on a yes/no or fingerprint/OTP choice (but not a stage that's
expecting free-text data like a name or IBAN, where those words could be
legitimate input).

### 8. Continuous conversation
The assistant remembers the current task across turns (`session['assistant_state']`
with a `flow`/`stage`), e.g.: "حول مبلغ" → "من ولمن؟" → "أحمد" → "كم؟" → "50" →
"هل تؤكد؟" → "نعم" → "بصمة أم رمز؟" → "بصمة" → opens `/auth-verify`, and completes
the transfer automatically the moment the fingerprint/OTP check succeeds.

### 9. Performance
- The ML intent model now loads on a **background thread at startup**
  (`nlu.warm_up()`), not on the first request.
- Pending actions expire after 10 minutes so nothing lingers indefinitely.
- No blocking/synchronous network calls anywhere in the request path (OTP,
  everything else is local file I/O + in-process inference).
- Static assets are cached (`Cache-Control: public, max-age=3600`).

## What was verified (automated, end-to-end)
- Full transfer via the gate + OTP, including a wrong-code rejection and a
  no-pending-action rejection (never hangs, always a clean error).
- Full transfer via the gate + a **real** WebAuthn signature (synthetic EC
  keypair, exactly the way a browser/authenticator would produce it) — and a
  **forged** signature from a different key is correctly rejected, confirming
  the transfer cannot execute without a genuine signature.
- Spoken-word OTP end-to-end (random 6-digit code → spoken Arabic words → sent
  as raw text → correctly converted and verified server-side).
- Beneficiary creation via the gate, plus negative tests: duplicate name,
  unknown beneficiary transfer target, and insufficient balance are all
  rejected with a clear message before any pending action is even created.
- The full voice conversation: spoken-number transfers ("حول عشرين ريال
  لأحمد" through "حول مئتين ريال لأحمد"), the multi-turn "transfer money" →
  beneficiary → amount → confirm → method-choice → auth-verify flow, the
  "أضف مستفيد" → name → IBAN → nickname → method-choice flow, and every voice
  navigation command.
- Every page loads (200 authenticated / 302 redirect-to-login unauthenticated),
  no Jinja/template errors, and all modified JS files (inline and external)
  pass a syntax check.

## Known, stated limitations (not hidden)
- OTP codes are generated locally and shown on-screen (`demo_code`) — no real
  SMS gateway is connected.
- WebAuthn attestation (verifying the authenticator hardware/vendor) isn't
  checked — only the assertion signature, challenge, origin, and RP binding,
  which is what actually matters for "is this really the enrolled device."
- The Arabic number-word parser covers units/tens/hundreds/thousands, which
  covers normal spoken transfer amounts; multi-word teens (e.g. "أحد عشر")
  aren't specially handled beyond "عشرة"=10, since they're rare in this context.
- "Next"/"Previous" voice commands are acknowledged but are no-ops — there's no
  paginated content in this app for them to act on.
