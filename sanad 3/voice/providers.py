#!/usr/bin/python3
"""
Provider-agnostic TTS backends for the سَند voice subsystem.

Every provider exposes the same two methods:
    synthesize(text, voice, speed) -> bytes (a complete audio clip, mp3)
    stream(text, voice, speed) -> generator[bytes] (audio chunks, for
        progressive playback)

Providers must NEVER let a missing package or network error propagate as an
uncaught exception from these methods when probed via `is_available()` —
callers (voice/tts.py) treat any exception from synthesize()/stream() as
"this provider is down right now", and move on to the next one in the
chain, ending in BrowserFallbackProvider (which does no server-side work at
all — it just signals the client to use its own Web Speech API voice,
exactly like the app already did before this subsystem existed).

Adding a new provider later (ElevenLabs, Azure, Google, etc.) means adding
one class here that implements this interface — nothing else in the app
needs to change.
"""

import os


class TTSUnavailable(Exception):
    """Raised by a provider when it cannot synthesize speech right now
    (missing package, missing credentials, network error, rate limit)."""


class TTSProvider:
    name = "base"

    def is_available(self) -> bool:
        raise NotImplementedError

    def synthesize(self, text: str, voice: str, speed: float) -> bytes:
        raise NotImplementedError

    def stream(self, text: str, voice: str, speed: float):
        """Default: providers that can't truly stream just yield their one
        complete clip as a single chunk — callers can treat stream() as
        always-available once synthesize() works."""
        yield self.synthesize(text, voice, speed)


# ---------------------------------------------------------------------------
# edge-tts — Microsoft's free neural voices. Excellent Arabic prosody
# (ar-SA-HamedNeural / ar-SA-ZariyahNeural), no API key required, genuinely
# streams natively (it talks to the service over a websocket and yields
# audio chunks as they're generated) — the best fit for low perceived
# latency. This is the preferred default provider.
# ---------------------------------------------------------------------------
DEFAULT_AR_VOICE_EDGE = "ar-SA-HamedNeural"
DEFAULT_AR_VOICE_EDGE_FEMALE = "ar-SA-ZariyahNeural"
DEFAULT_EN_VOICE_EDGE = "en-US-GuyNeural"
DEFAULT_EN_VOICE_EDGE_FEMALE = "en-US-JennyNeural"


class EdgeTTSProvider(TTSProvider):
    name = "edge_tts"

    def is_available(self) -> bool:
        try:
            import edge_tts  # noqa: F401
            return True
        except ImportError:
            return False

    @staticmethod
    def _rate_string(speed: float) -> str:
        # edge-tts takes a relative rate like "+20%" / "-10%", not an
        # absolute multiplier — convert our 0.5-2.0 speed scale into that.
        pct = round((speed - 1.0) * 100)
        sign = "+" if pct >= 0 else ""
        return f"{sign}{pct}%"

    def synthesize(self, text: str, voice: str, speed: float) -> bytes:
        chunks = list(self.stream(text, voice, speed))
        return b"".join(chunks)

    def stream(self, text: str, voice: str, speed: float):
        try:
            import edge_tts
        except ImportError as e:
            raise TTSUnavailable(f"edge_tts not installed: {e}")

        import asyncio

        async def _collect():
            communicate = edge_tts.Communicate(text, voice=voice, rate=self._rate_string(speed))
            out = []
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio":
                    out.append(chunk["data"])
            return out

        try:
            # asyncio.run() (not manual new_event_loop()/run_until_complete()/
            # close()) is the officially recommended pattern for running a
            # coroutine from sync code: it correctly registers AND tears down
            # the event loop for the calling thread every time, which matters
            # here because this runs inside a Flask request-handler thread
            # (not the main thread), potentially many times across many
            # requests — manual loop management is a well-known source of
            # "works once, breaks on reuse" / "attached to a different loop"
            # bugs in exactly this kind of repeated sync-calls-async setup.
            audio_chunks = asyncio.run(_collect())
        except Exception as e:
            # Preserve and surface the REAL underlying error — this used to
            # be swallowed with no server-side trace at all, making a broken
            # edge-tts install indistinguishable from "not installed" with
            # zero way to debug it. See voice/tts.py's logging of this.
            raise TTSUnavailable(f"edge_tts error ({type(e).__name__}): {e}")

        if not audio_chunks:
            raise TTSUnavailable("edge_tts returned no audio chunks")
        for chunk in audio_chunks:
            yield chunk


# ---------------------------------------------------------------------------
# OpenAI TTS — reuses the exact same client/API key already required for
# the AI fallback layer (Step 2). Good alternative/secondary provider,
# especially if a deployment prefers a single vendor.
# ---------------------------------------------------------------------------
DEFAULT_AR_VOICE_OPENAI = "onyx"
DEFAULT_EN_VOICE_OPENAI = "alloy"


class OpenAITTSProvider(TTSProvider):
    name = "openai"

    def is_available(self) -> bool:
        if not os.environ.get("OPENAI_API_KEY"):
            return False
        try:
            import openai  # noqa: F401
            return True
        except ImportError:
            return False

    def _get_client(self):
        try:
            from openai import OpenAI
        except ImportError as e:
            raise TTSUnavailable(f"openai package not installed: {e}")
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise TTSUnavailable("OPENAI_API_KEY not set")
        try:
            return OpenAI(api_key=api_key, timeout=15)
        except Exception as e:
            raise TTSUnavailable(f"failed to construct OpenAI client: {e}")

    def synthesize(self, text: str, voice: str, speed: float) -> bytes:
        client = self._get_client()
        try:
            response = client.audio.speech.create(
                model="tts-1",
                voice=voice,
                input=text,
                speed=max(0.25, min(4.0, speed)),
            )
            return response.read() if hasattr(response, "read") else response.content
        except Exception as e:
            raise TTSUnavailable(f"OpenAI TTS error: {e}")

    def stream(self, text: str, voice: str, speed: float):
        client = self._get_client()
        try:
            with client.audio.speech.with_streaming_response.create(
                model="tts-1",
                voice=voice,
                input=text,
                speed=max(0.25, min(4.0, speed)),
            ) as response:
                for chunk in response.iter_bytes(chunk_size=4096):
                    if chunk:
                        yield chunk
        except Exception as e:
            raise TTSUnavailable(f"OpenAI TTS streaming error: {e}")


# ---------------------------------------------------------------------------
# Browser fallback — not a real synthesis backend. Its presence in the
# provider chain just means "give up server-side and let the client use
# its own Web Speech API voice", i.e. exactly today's pre-existing
# behavior. Always "available" so the chain always has a safe end state.
# ---------------------------------------------------------------------------
class BrowserFallbackProvider(TTSProvider):
    name = "browser"

    def is_available(self) -> bool:
        return True

    def synthesize(self, text: str, voice: str, speed: float) -> bytes:
        raise TTSUnavailable("browser provider has no server-side audio by design")

    def stream(self, text: str, voice: str, speed: float):
        raise TTSUnavailable("browser provider has no server-side audio by design")
        yield b""  # pragma: no cover - unreachable, keeps this a generator


PROVIDER_REGISTRY = {
    "edge": EdgeTTSProvider,
    "openai": OpenAITTSProvider,
    "browser": BrowserFallbackProvider,
}
