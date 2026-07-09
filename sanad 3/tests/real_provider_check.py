#!/usr/bin/python3
"""
Run this in your REAL environment (with internet access and
`pip install edge-tts openai` done) to verify the voice subsystem against
actual neural TTS providers — this cannot be run in the development
sandbox used to build this feature (no internet there).

Usage:
    cd sanad/  (the project root, so `voice/` is importable)
    python3 tests/real_provider_check.py

What it checks:
    1. edge-tts actually returns real audio for Arabic + English text.
    2. OpenAI TTS actually returns real audio (if OPENAI_API_KEY is set).
    3. Real synthesis latency, and the real cache-hit speedup on top of it.
    4. Real streaming: measures actual time-to-first-chunk vs total time.
    5. Saves one sample clip per provider to disk so you can listen to it
       directly and judge voice quality/prosody yourself.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import voice.tts as tts
import voice.cache as cache
from voice.providers import EdgeTTSProvider, OpenAITTSProvider

SAMPLE_TEXT_AR = "مرحباً بك في تطبيق سَند. رصيدك الحالي خمسة عشر ألف ريال."
SAMPLE_TEXT_EN = "Welcome to the Sanad banking assistant. This is a voice quality test."

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_samples")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def check_provider(name, provider, account):
    print(f"\n--- {name} ---")
    if not provider.is_available():
        print(f"  SKIP: {name} is not available (package not installed or no API key)")
        return

    cache.clear()
    account["voice_settings"]["provider"] = provider.name

    for label, text in [("arabic", SAMPLE_TEXT_AR), ("english", SAMPLE_TEXT_EN)]:
        try:
            t0 = time.perf_counter()
            result = tts.synthesize(text, account, lang=label[:2])
            fresh_time = time.perf_counter() - t0
            if result is None:
                print(f"  FAIL: {label} synthesis returned None (provider silently unavailable)")
                continue
            audio_bytes, mime = result
            print(f"  PASS: {label} synthesis succeeded — {len(audio_bytes)} bytes, {fresh_time*1000:.0f}ms, mime={mime}")

            out_path = os.path.join(OUTPUT_DIR, f"{provider.name}_{label}.mp3")
            with open(out_path, "wb") as f:
                f.write(audio_bytes)
            print(f"        saved to {out_path} — LISTEN TO THIS to judge voice quality")

            t0 = time.perf_counter()
            result2 = tts.synthesize(text, account, lang=label[:2])
            cached_time = time.perf_counter() - t0
            speedup = fresh_time / max(cached_time, 1e-6)
            print(f"  PASS: {label} cache hit — {cached_time*1000:.1f}ms ({speedup:.0f}x faster than fresh)")

        except Exception as e:
            print(f"  FAIL: {label} raised an exception: {e}")

    # Streaming timing
    try:
        cache.clear()
        t0 = time.perf_counter()
        first_chunk_time = None
        chunk_count = 0
        for chunk in tts.stream_synthesize(SAMPLE_TEXT_AR, account, lang="ar"):
            if first_chunk_time is None:
                first_chunk_time = time.perf_counter() - t0
            chunk_count += 1
        total_time = time.perf_counter() - t0
        print(f"  PASS: streaming — {chunk_count} chunks, first chunk at {first_chunk_time*1000:.0f}ms, total {total_time*1000:.0f}ms")
        if first_chunk_time < total_time * 0.7:
            print(f"        streaming reduces perceived latency (first chunk well before total completion)")
        else:
            print(f"        WARNING: first chunk arrived close to total time — provider may not be truly streaming")
    except Exception as e:
        print(f"  FAIL: streaming raised an exception: {e}")


def main():
    print("=" * 70)
    print("REAL PROVIDER CHECK — requires internet access")
    print("=" * 70)

    account = {"name": "Test", "voice_settings": {"provider": "auto", "speed": 1.0, "gender": "male"}}

    check_provider("edge-tts", EdgeTTSProvider(), account)
    check_provider("OpenAI TTS", OpenAITTSProvider(), account)

    print("\n" + "=" * 70)
    print(f"Sample audio files (if any were generated) are in: {OUTPUT_DIR}")
    print("Listen to them directly to judge pronunciation, prosody, and emotion.")
    print("=" * 70)


if __name__ == "__main__":
    main()
