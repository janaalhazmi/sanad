#!/usr/bin/python3
"""Verifies voice/tts.py's orchestration logic (caching, provider chain,
settings resolution, streaming) using a fake in-process provider standing
in for real edge-tts/OpenAI audio — since this sandbox has no internet to
reach either real service. This tests the LOGIC honestly; it does not (and
cannot) prove real edge-tts/OpenAI audio quality — that needs a live check
in an environment with network access."""
import sys
sys.path.insert(0, "/home/claude/sanad")

import voice.tts as tts
import voice.cache as cache
from voice.providers import TTSProvider, TTSUnavailable


class FakeProvider(TTSProvider):
    name = "edge"  # pretend to be edge-tts, the default-preferred provider

    def __init__(self):
        self.call_count = 0
        self.fail = False

    def is_available(self):
        return True

    def synthesize(self, text, voice, speed):
        self.call_count += 1
        if self.fail:
            raise TTSUnavailable("simulated failure")
        return f"AUDIO[{voice}|{speed}|{text}]".encode()

    def stream(self, text, voice, speed):
        self.call_count += 1
        if self.fail:
            raise TTSUnavailable("simulated stream failure")
        # simulate 3 chunks
        payload = f"AUDIO[{voice}|{speed}|{text}]".encode()
        third = max(1, len(payload) // 3)
        yield payload[:third]
        yield payload[third:2 * third]
        yield payload[2 * third:]


fake = FakeProvider()
tts._provider_instances["edge"] = fake
tts._provider_instances["openai"].is_available = lambda: False  # isolate to just the fake

cache.clear()
results = []


def check(name, cond, detail=""):
    results.append((name, cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


account = {"name": "Jana", "voice_settings": {"provider": "auto", "speed": 1.0, "gender": "male"}}

# 1. Basic synthesis works and returns correct mime
r = tts.synthesize("مرحباً بك", account)
check("synthesize() returns (bytes, mime) when a provider is available", r is not None and r[1] == "audio/mpeg")
check("audio bytes reflect the resolved voice/speed/text", b"mrhb" not in r[0] and r[0].startswith(b"AUDIO["), r[0][:40])

# 2. Caching: second identical call must NOT hit the provider again
calls_before = fake.call_count
r2 = tts.synthesize("مرحباً بك", account)
check("second identical call is served from cache (no extra provider call)", fake.call_count == calls_before, f"calls={fake.call_count}")
check("cached bytes are identical", r2[0] == r[0])

# 3. Changing speed changes the cache key (different audio expected)
account_fast = {"name": "Jana", "voice_settings": {"provider": "auto", "speed": 1.5, "gender": "male"}}
calls_before = fake.call_count
r3 = tts.synthesize("مرحباً بك", account_fast)
check("different speed setting -> cache MISS -> provider called again", fake.call_count == calls_before + 1)
check("different speed -> different audio bytes", r3[0] != r[0])

# 4. Gender setting changes resolved voice (male vs female edge voice)
account_female = {"name": "Jana", "voice_settings": {"provider": "auto", "speed": 1.0, "gender": "female"}}
r4 = tts.synthesize("مرحباً بك", account_female)
check("female gender setting resolves to the female edge voice", b"ZariyahNeural" in r4[0], r4[0][:60])
check("male (default) resolves to the male edge voice", b"HamedNeural" in r[0], r[0][:60])

# 5. Streaming yields multiple chunks that reassemble correctly
cache.clear()
chunks = list(tts.stream_synthesize("اختبار البث", account))
check("stream_synthesize yields multiple chunks", len(chunks) >= 2, f"{len(chunks)} chunks")
reassembled = b"".join(chunks)
check("reassembled stream matches a direct synthesize() call", reassembled == tts.synthesize("اختبار البث", account)[0])

# 6. Provider failure -> graceful None / TTSUnavailable, no crash
cache.clear()
fake.fail = True
r5 = tts.synthesize("نص آخر", account)
check("provider failure -> synthesize() returns None (fallback signal), not a crash", r5 is None)
try:
    list(tts.stream_synthesize("نص آخر ٢", account))
    check("provider failure -> stream raises TTSUnavailable", False)
except TTSUnavailable:
    check("provider failure -> stream raises TTSUnavailable", True)
fake.fail = False

print("\n=== SUMMARY ===")
print("ALL PASS" if all(c for _, c in results) else "SOME FAILED")
