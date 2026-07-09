#!/usr/bin/python3
"""
Full pipeline test: voice.providers.EdgeTTSProvider -> voice.tts.synthesize()
-> the same code path /api/tts calls, using a realistic fake `edge_tts`
module (no internet here to install the real package). Proves:
  1. A working edge_tts install actually gets used and returns real audio
     bytes end-to-end (not a silent fallback).
  2. When edge_tts genuinely fails, the real underlying error is logged
     (not silently swallowed) — this is what makes a REAL environment's
     actual failure debuggable, since we can't reproduce their exact
     failure without their environment.
  3. Confirms account voice_settings correctly select edge_tts under the
     default "auto" preference.
"""
import io
import logging
import sys
import types

sys.path.insert(0, "/home/claude/sanad")

# ---- Capture voice.tts's logger output so we can assert on it ----
log_capture = io.StringIO()
handler = logging.StreamHandler(log_capture)
handler.setFormatter(logging.Formatter("%(message)s"))

import voice.tts as tts
tts.logger.addHandler(handler)
tts.logger.setLevel(logging.DEBUG)

import voice.providers as providers
import voice.cache as cache

results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


def install_fake_edge_tts(should_fail=False, fail_message="simulated Microsoft endpoint error"):
    fake_module = types.ModuleType("edge_tts")

    class FakeCommunicate:
        def __init__(self, text, voice=None, rate=None):
            self.text = text
            self.voice = voice
            self.rate = rate

        async def stream(self):
            if should_fail:
                raise ConnectionError(fail_message)
            # Real edge-tts yields several chunks for a real clip; simulate that.
            payload = f"REAL_EDGE_TTS_AUDIO[{self.voice}|{self.rate}|{self.text}]".encode()
            third = max(1, len(payload) // 3)
            for i in range(0, len(payload), third):
                yield {"type": "audio", "data": payload[i:i + third]}
            yield {"type": "WordBoundary", "data": b""}  # non-audio chunks must be ignored, not crash

    fake_module.Communicate = FakeCommunicate
    sys.modules["edge_tts"] = fake_module


# ================================================================
# TEST 1: edge_tts installed and working -> real audio, no fallback
# ================================================================
install_fake_edge_tts(should_fail=False)
cache.clear()
log_capture.truncate(0)
log_capture.seek(0)

account = {"name": "Jana", "voice_settings": {"provider": "auto", "speed": 1.0, "gender": "male"}}
result = tts.synthesize("مرحباً بك في سَند", account, lang="ar")

check("synthesize() succeeds through edge_tts (not None/browser-fallback)", result is not None, result)
if result:
    audio, mime = result
    check("returned bytes are real synthesized audio (from the fake edge_tts)", b"REAL_EDGE_TTS_AUDIO" in audio, audio[:60])
    check("mime type is audio/mpeg", mime == "audio/mpeg")
    check("non-audio chunks (WordBoundary) were correctly filtered out", b"WordBoundary" not in audio)

check("no 'falling back to browser' log line was emitted on success", "falling back to browser" not in log_capture.getvalue())

# ================================================================
# TEST 2: edge_tts genuinely fails -> real error message is LOGGED
# (this is what makes a real environment's actual failure debuggable)
# ================================================================
install_fake_edge_tts(should_fail=True, fail_message="Sec-MS-GEC token rejected by Microsoft endpoint")
cache.clear()
log_capture.truncate(0)
log_capture.seek(0)

result2 = tts.synthesize("نص آخر", account, lang="ar")
log_output = log_capture.getvalue()

check("synthesize() falls back gracefully (returns None) on a real failure", result2 is None)
check(
    "the REAL underlying error message is actually logged (not silently swallowed)",
    "Sec-MS-GEC token rejected" in log_output,
    log_output.strip()[-200:],
)
check("the failing provider's name appears in the log", "edge_tts" in log_output)

# ================================================================
# TEST 3: "auto" preference genuinely tries edge first
# ================================================================
install_fake_edge_tts(should_fail=False)
cache.clear()
chain = tts._provider_chain("auto")
check("'auto' preference resolves edge_tts as the first available provider", len(chain) >= 1 and chain[0].name == "edge_tts", [p.name for p in chain])

print("\n=== SUMMARY ===")
print("ALL PASS" if all(results) else "SOME FAILED")
