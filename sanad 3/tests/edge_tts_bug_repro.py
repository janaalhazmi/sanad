#!/usr/bin/python3
"""
Reproduces the real bug: edge-tts's Communicate.stream() uses aiohttp
under the hood for its websocket connection, and aiohttp's internals call
asyncio.get_event_loop() in places. Flask's dev server (and most WSGI
servers) handle each request in a worker THREAD, not the main thread. In
Python 3.10+, calling asyncio.get_event_loop() from a non-main thread with
no event loop explicitly registered for that thread (via
asyncio.set_event_loop()) raises RuntimeError.

The original EdgeTTSProvider.stream() did:
    loop = asyncio.new_event_loop()
    loop.run_until_complete(_collect())
    loop.close()

...WITHOUT ever calling asyncio.set_event_loop(loop) — so a fresh loop
runs the coroutine, but any code inside that coroutine which itself calls
asyncio.get_event_loop() (exactly what aiohttp does) does NOT see the loop
we just created for THIS thread, and raises. This silently gets caught by
the provider's broad `except Exception as e: raise TTSUnavailable(...)`,
producing exactly the reported symptom: edge-tts is installed and would
otherwise work, but every single call silently falls back.

This test proves it with a fake `edge_tts` module (we have no internet to
install the real package here) that mimics that exact aiohttp behavior,
run from a background THREAD (not the main thread) to match how Flask's
threaded dev server actually handles requests.
"""
import asyncio
import sys
import threading
import types


# ---- Fake edge_tts module mimicking the real aiohttp-internals gotcha ----
fake_edge_tts = types.ModuleType("edge_tts")


class FakeCommunicate:
    def __init__(self, text, voice=None, rate=None):
        self.text = text
        self.voice = voice
        self.rate = rate

    async def stream(self):
        # This is exactly what aiohttp's internals effectively do: rely on
        # asyncio.get_event_loop() returning THE loop actually driving this
        # coroutine right now, for the calling thread.
        try:
            asyncio.get_event_loop()
        except RuntimeError as e:
            raise RuntimeError(f"aiohttp-style internal failure: {e}")
        yield {"type": "audio", "data": b"FAKE_MP3_BYTES_FROM_EDGE_TTS"}


fake_edge_tts.Communicate = FakeCommunicate
sys.modules["edge_tts"] = fake_edge_tts

sys.path.insert(0, "/home/claude/sanad")


def run_in_worker_thread(fn):
    """Runs fn() in a background thread (like Flask's per-request worker
    thread) and returns (result, exception)."""
    result = {}

    def target():
        try:
            result["value"] = fn()
        except Exception as e:
            result["error"] = e

    t = threading.Thread(target=target)
    t.start()
    t.join()
    return result.get("value"), result.get("error")


def call_edge_provider():
    # Re-import fresh each time to avoid any module-level caching hiding
    # the bug between the "before" and "after" runs.
    import importlib
    import voice.providers as providers
    importlib.reload(providers)
    provider = providers.EdgeTTSProvider()
    return provider.synthesize("مرحباً", "ar-SA-HamedNeural", 1.0)


print("=== BEFORE FIX: calling EdgeTTSProvider from a worker thread (like Flask) ===")
value, error = run_in_worker_thread(call_edge_provider)
if error:
    print(f"FAIL (reproduces the bug): {type(error).__name__}: {error}")
else:
    print(f"unexpectedly succeeded: {value}")
