# سَند (Sanad) — AI Banking Assistant

A demo AI-powered banking assistant built for the Alinma Bank Hackathon. A Flask backend
serves a bilingual (Arabic/English) banking demo with a local NLU layer, an optional
OpenAI-powered fallback, a pluggable neural voice subsystem, optional voice-biometric
authentication, a full accessibility system (Senior Mode), a wake-word assistant, light/dark
themes, and hardened WebAuthn + OTP transaction security.

**This is a hackathon/demo project — `account.json`/`beneficiaries.json` are flat-file
"databases" seeded with fake data, not a real banking backend.**

---

## Quick start

```bash
pip install -r requirements.txt
python3 app_server.py
```

Open `http://localhost:5001` (not `127.0.0.1` — the app now auto-redirects
127.0.0.1/::1 to `localhost` anyway, because WebAuthn/fingerprint auth
requires a valid RP ID and IP addresses are never valid RP IDs, only
`localhost` and real domains are). Demo login: phone `0500000000`, PIN
`12345`.

If you've just pulled UI/CSS changes and the browser still shows the old
look after restarting the server, hard-refresh (Cmd+Shift+R / Ctrl+Shift+R)
once — static assets are now cache-busted per server start, but a
long-lived browser tab open from before the restart can still be showing
a previously cached response.

## Environment variables

| Variable | Required? | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | Optional | Enables the AI fallback layer (Step 2) — GPT-5 if available, else GPT-4.1. Without it, the assistant still works via the local NLU (`assistant_nlu.py`), just without general Q&A/recommendations/travel-planning beyond the trained intents. |
| `SANAD_SECRET_KEY` | **Strongly recommended** | Flask session-signing key. Without it, a built-in demo key is used and a warning is printed at startup — fine for local demo, **not safe for any real deployment**. Set to a long random value: `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `SANAD_DEBUG` | Optional | Set to `true` to enable Flask's debug mode/reloader for local development. **Never enable in production** — it exposes an interactive code-execution debugger. Defaults to `false`. |

## Architecture

```
app_server.py          Flask routes: pages, auth, pending-action security gate, all APIs
assistant_nlu.py        Fast local intent classifier (TF-IDF/NaiveBayes) + regex extraction
                         + confidence scoring (decides when to escalate to the AI layer)
transfer.py / beneficiary.py / otp.py   Core banking + OTP data managers

ai/                      OpenAI Responses API fallback (Step 2) — ONLY ever proposes
  ai_engine.py            actions via forced tool-calling; app_server.py routes every
  conversation.py         proposal through the SAME pending_action -> OTP/WebAuthn gate
  memory.py               used by the local flow. Never executes anything itself.
  prompt.py
  responses.py
  tools.py / banking_tools.py

voice/                   Neural TTS (Step 3) — pluggable providers (edge-tts primary,
  providers.py            OpenAI TTS secondary), graceful fallback to the browser's own
  tts.py                  speechSynthesis if neither is installed/configured. Includes
  settings.py             an in-process audio cache and a streaming synthesis path.
  cache.py / streaming.py

voice_auth/              Optional voice-biometric authentication (Step 4), ALWAYS an
  embeddings.py           ADDITIONAL factor stacked on top of OTP/WebAuthn, never a
  enrollment.py           replacement. Stores only speaker embeddings (never raw audio).
  verification.py         Fails CLOSED: if enabled but the embedding provider is
  storage.py / settings.py  unavailable, the transaction is blocked, not silently allowed.
  engine.py

accessibility/           Senior Mode + accessibility preferences (Step 5) — settings
  settings.py             only; the actual sizing/contrast lives in CSS classes applied
  engine.py               server-side via a Flask context processor (no flash of
                          unstyled content, no route handlers touched).

templates/, static/       Jinja templates + vanilla JS/CSS. Light/dark theme (Step 6) via
                          a `data-theme` attribute, same context-processor pattern as
                          accessibility. Wake word (Milestone: Wake Word) is a pure
                          client-side SpeechRecognition layer that triggers the existing
                          mic button — no duplicated command-handling logic.

tests/                   All test scripts (see Testing below).
```

## Security model (do not weaken without careful review)

Every sensitive action (transfer, add beneficiary) always goes through:

```
pending_action created
        |
   OTP  or  WebAuthn            <- primary factor, user's choice if WebAuthn enrolled
        | (success)
Voice Authentication            <- ONLY if the user has enrolled AND enabled it;
        | (only if enabled)        skipped entirely otherwise, unchanged from before
        v
_execute_pending_action()       <- the ONLY place that moves money / adds a beneficiary
```

The AI fallback layer can only ever *propose* a transfer/add-beneficiary via a forced tool
call; it is never on the execution path. Voice Authentication fails **closed**: if enabled
but the embedding provider is unavailable, the action is blocked, not silently permitted.

WebAuthn challenges are single-use with a TTL, and a credential's enrollment RP-ID is
checked at verification time to give a clear diagnostic if the app is accessed from a
different host than it was enrolled on (the most common real-world cause of "enrolled fine,
login fails").

## Testing

All test scripts are in `tests/`. Start the server, then run:

```bash
python3 tests/webauthn_probe.py                  # WebAuthn register/login/action, real crypto
python3 tests/webauthn_hardening_probe.py        # challenge TTL, replay protection, RP-ID diagnostics
python3 tests/acceptance_section1_existing.py    # full app walkthrough incl. a real OTP transfer
python3 tests/acceptance_section2_voice_settings.py
python3 tests/voice_api_probe.py                 # TTS settings + graceful degradation
python3 tests/voice_auth_math_probe.py           # enrollment/verification math, FAR/FRR sanity
python3 tests/voice_auth_engine_probe.py         # fail-closed policy proof
python3 tests/voice_auth_gate_probe.py           # full security-gate integration, live server
node tests/voice_player_js_probe.js              # real JS execution: race conditions, memory leaks
node tests/voice_browser_fallback_probe.js       # real JS execution: browser TTS fallback

# AI fallback tests need the mock server (no real OpenAI network call):
PYTHONPATH=. python3 tests/mock_ai_server.py &
python3 tests/ai_fallback_probe.py
```

**Once you have real credentials/packages installed**, also run:

```bash
python3 tests/real_provider_check.py    # real edge-tts/OpenAI TTS — saves sample audio to listen to
python3 tests/ai_live_probe.py          # real, live end-to-end OpenAI check (see below)
```

### Verifying the OpenAI integration actually works end-to-end

`/health`'s `ai_diagnostics` only checks *configuration* (package installed, key present) —
it never makes a network call, so it can say everything looks fine even if the key is
invalid or the network can't reach OpenAI. For a real, definitive answer:

```bash
python3 app_server.py            # terminal 1
python3 tests/ai_live_probe.py   # terminal 2
```

This logs in, hits `/api/ai/diagnose` (places one real OpenAI Responses API call and prints
the literal response or the exact underlying error — auth/network/model/rate-limit), then
sends a real open-ended question ("كيف أطلع قرض؟") through `/api/assistant` and asserts the
answer is a genuine AI response, not the local NLU's generic fallback text. You can also hit
`GET /api/ai/diagnose` directly (while logged in) any time for the same live check.

### A note on test data

Tests write to `account.json`/`beneficiaries.json` (this is a flat-file demo "database").
Reset to clean state between runs if needed — the original seed data is whatever you first
received; keep a backup copy if you want to reliably reset.

## Known limitations (be aware of these before a live demo)

- **No real voice-biometric or neural-TTS testing has been done against live services** in
  this repo's development environment (it had no internet access). The math/security-gate
  logic is thoroughly tested against fake providers; real audio quality/accuracy needs a
  live check with `speechbrain`/`edge-tts` actually installed and a real microphone.
- **WebAuthn requires HTTPS or exactly `localhost`** — browsers block the API entirely on
  plain-HTTP LAN IPs or tunnel URLs. If a demo device can't complete fingerprint auth, this
  is almost always why.
- Continuous wake-word listening is inherently a bit fragile across browsers (auto-stops
  after silence, competes for the microphone) — it's built to restart itself, but a live
  test on the actual demo device/browser is worth doing beforehand.
- There is no real bill-payment feature/data model in this demo; the AI assistant is
  instructed to say so honestly rather than pretend to process one.
