#!/usr/bin/python3
"""
Section 4/6 proxy test: since this sandbox has no internet (confirmed via
a failed `pip install edge-tts` attempt), we cannot measure real edge-tts/
OpenAI network latency here. This test instead uses a FAKE provider with a
deliberately realistic artificial delay (150ms per synthesis call, chunked
into 5 pieces for streaming) to honestly prove the MECHANISMS work:
  - a cache hit is dramatically faster than a fresh synthesis
  - streaming yields the first chunk well before the full clip is ready
This is a proxy for the real thing, not a replacement — see the
companion script `real_provider_check.py` for what to run against actual
edge-tts/OpenAI once installed with network access.
"""
import sys
import time

sys.path.insert(0, "/home/claude/sanad")
import voice.tts as tts
import voice.cache as cache
from voice.providers import TTSProvider


class RealisticFakeProvider(TTSProvider):
    name = "edge"

    def __init__(self, delay_seconds=0.15):
        self.delay = delay_seconds
        self.call_count = 0

    def is_available(self):
        return True

    def synthesize(self, text, voice, speed):
        self.call_count += 1
        time.sleep(self.delay)
        return f"AUDIO[{voice}|{text}]".encode() * 200  # non-trivial size

    def stream(self, text, voice, speed):
        self.call_count += 1
        payload = f"AUDIO[{voice}|{text}]".encode() * 200
        chunk_size = max(1, len(payload) // 5)
        for i in range(0, len(payload), chunk_size):
            time.sleep(self.delay / 5)  # each chunk takes 1/5 as long to arrive
            yield payload[i:i + chunk_size]


fake = RealisticFakeProvider(delay_seconds=0.15)
tts._provider_instances["edge"] = fake
tts._provider_instances["openai"].is_available = lambda: False
cache.clear()

results = []


def check(name, cond, detail=""):
    results.append((name, cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


account = {"name": "Jana", "voice_settings": {"provider": "auto", "speed": 1.0, "gender": "male"}}
text = "مرحباً بك في سَند، هذا اختبار لقياس زمن الاستجابة"

# ---- Fresh synthesis timing ----
t0 = time.perf_counter()
result1 = tts.synthesize(text, account)
fresh_time = time.perf_counter() - t0
check("fresh synthesis succeeds", result1 is not None)
print(f"    fresh synthesis time: {fresh_time*1000:.1f}ms")

# ---- Cached synthesis timing (same text/settings) ----
t0 = time.perf_counter()
result2 = tts.synthesize(text, account)
cached_time = time.perf_counter() - t0
check("cache hit is dramatically faster than fresh synthesis", cached_time < fresh_time / 5,
      f"cached={cached_time*1000:.2f}ms vs fresh={fresh_time*1000:.1f}ms")
check("cached audio bytes are identical to the original", result2[0] == result1[0])
print(f"    cached synthesis time: {cached_time*1000:.2f}ms  (speedup: {fresh_time/max(cached_time,1e-9):.0f}x)")

# ---- Streaming: time to first chunk vs total time ----
cache.clear()
t0 = time.perf_counter()
first_chunk_time = None
chunk_count = 0
for chunk in tts.stream_synthesize(text + " مختلف قليلاً", account):
    if first_chunk_time is None:
        first_chunk_time = time.perf_counter() - t0
    chunk_count += 1
total_stream_time = time.perf_counter() - t0

check("streaming yields multiple chunks", chunk_count >= 3, f"chunks={chunk_count}")
check(
    "time-to-first-chunk is meaningfully less than total synthesis time",
    first_chunk_time < total_stream_time * 0.6,
    f"first_chunk={first_chunk_time*1000:.1f}ms total={total_stream_time*1000:.1f}ms",
)
print(f"    time to first audio chunk: {first_chunk_time*1000:.1f}ms")
print(f"    total streaming synthesis time: {total_stream_time*1000:.1f}ms")

print("\n=== SUMMARY (PROXY TEST — fake provider with artificial delay, NOT real edge-tts/OpenAI) ===")
print("ALL PASS" if all(c for _, c in results) else "SOME FAILED")
print("\nThis proves the caching and streaming MECHANISMS work correctly.")
print("It does NOT prove real edge-tts/OpenAI latency or audio quality —")
print("run real_provider_check.py (same directory) in an environment with")
print("internet access and edge-tts/openai installed for that.")
