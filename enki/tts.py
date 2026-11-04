"""TTS port + adapters. Same shape as llm.py: one Protocol, N adapters, a failover chain.

Every adapter returns WAV/AIFF bytes; playback is a separate concern (Player).
Cloud adapters use raw httpx (no vendor SDKs) -> one dependency, easy to mock.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Protocol
from xml.sax.saxutils import escape

import httpx

log = logging.getLogger("enki.tts")


@dataclass(frozen=True, slots=True)
class Audio:
    data: bytes
    ext: str  # "wav" | "aiff" | "mp3"


class TTS(Protocol):
    name: str

    async def synth(self, text: str) -> Audio: ...


# ---------------- cloud ----------------
class OpenAITTS:
    """gpt-4o-mini-tts: the only one here you can *direct* like an actor via `instructions`."""

    name = "openai"
    INSTRUCTIONS = (
        "Türkçe konuş. Sakin, kuru ve hafif ironik bir ton; JARVIS gibi kendinden emin, "
        "ama arada belli belirsiz gülümseyen biri. Acele etme, cümle sonlarında kısa duraklar ver."
    )

    def __init__(self, api_key: str, voice: str = "onyx", http: httpx.AsyncClient | None = None):
        self.api_key, self.voice = api_key, voice
        self.http = http or httpx.AsyncClient(timeout=30)

    async def synth(self, text: str) -> Audio:
        r = await self.http.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": "gpt-4o-mini-tts",
                "voice": self.voice,
                "input": text,
                "instructions": os.getenv("ENKI_TTS_INSTRUCTIONS", self.INSTRUCTIONS),
                "response_format": "wav",
            },
        )
        r.raise_for_status()
        return Audio(r.content, "wav")


class GoogleTTS:
    name = "google"

    def __init__(self, api_key: str, voice: str = "tr-TR-Chirp3-HD-Charon",
                 http: httpx.AsyncClient | None = None):
        self.api_key, self.voice = api_key, voice
        self.http = http or httpx.AsyncClient(timeout=30)

    async def synth(self, text: str) -> Audio:
        r = await self.http.post(
            "https://texttospeech.googleapis.com/v1/text:synthesize",
            params={"key": self.api_key},
            json={
                "input": {"text": text},
                "voice": {"languageCode": "tr-TR", "name": self.voice},
                "audioConfig": {"audioEncoding": "LINEAR16"},
            },
        )
        r.raise_for_status()
        return Audio(base64.b64decode(r.json()["audioContent"]), "wav")


class AzureTTS:
    name = "azure"

    def __init__(self, key: str, region: str, voice: str = "tr-TR-AhmetNeural",
                 http: httpx.AsyncClient | None = None):
        self.key, self.region, self.voice = key, region, voice
        self.http = http or httpx.AsyncClient(timeout=30)

    async def synth(self, text: str) -> Audio:
        ssml = (
            "<speak version='1.0' xml:lang='tr-TR'>"
            f"<voice name='{self.voice}'>{escape(text)}</voice></speak>"
        )
        r = await self.http.post(
            f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1",
            headers={
                "Ocp-Apim-Subscription-Key": self.key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm",
                "User-Agent": "enki",
            },
            content=ssml.encode(),
        )
        r.raise_for_status()
        return Audio(r.content, "wav")


# ---------------- local ----------------
class PiperTTS:
    """Offline. Runs on the Pi 5 later. CPU-bound -> executed in a worker thread."""

    name = "piper"

    def __init__(self, model_path: str):
        from piper import PiperVoice  # pip install piper-tts

        self.voice = PiperVoice.load(model_path)

    def _synth_blocking(self, text: str) -> bytes:
        import io
        import wave

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            if hasattr(self.voice, "synthesize_wav"):  # piper-tts >= 1.3
                self.voice.synthesize_wav(text, w)
            else:  # older API
                self.voice.synthesize(text, w)
        return buf.getvalue()

    async def synth(self, text: str) -> Audio:
        return Audio(await asyncio.to_thread(self._synth_blocking, text), "wav")


class MacSayTTS:
    """Zero-setup baseline: macOS built-in voice. The 'boring robot' reference point."""

    name = "say"

    def __init__(self, voice: str = "Yelda"):
        self.voice = voice

    async def synth(self, text: str) -> Audio:
        with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as f:
            path = f.name
        try:
            p = await asyncio.create_subprocess_exec(
                "say", "-v", self.voice, "-o", path, text,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await p.communicate()
            if p.returncode != 0:
                raise RuntimeError(err.decode().strip() or "say failed")
            with open(path, "rb") as fh:
                return Audio(fh.read(), "aiff")
        finally:
            os.unlink(path)


class EdgeTTS:
    """Unofficial (Edge 'Read aloud' endpoint). Free, no key, Azure neural voices.
    Can break or be shut off any day -> fine for auditioning, risky as Enki's only voice."""

    def __init__(self, voice: str = "tr-TR-AhmetNeural", rate: str = "+0%", pitch: str = "+0Hz"):
        import edge_tts  # pip install edge-tts

        self._mod = edge_tts
        self.voice, self.rate, self.pitch = voice, rate, pitch
        self.name = "edge-" + voice.split("-")[2].removesuffix("Neural").lower()

    async def synth(self, text: str) -> Audio:
        c = self._mod.Communicate(text, self.voice, rate=self.rate, pitch=self.pitch)
        data = bytearray()
        async for chunk in c.stream():
            if chunk["type"] == "audio":
                data += chunk["data"]
        if not data:
            raise RuntimeError("edge-tts returned no audio")
        return Audio(bytes(data), "mp3")


# ---------------- composition ----------------
class FailoverTTS:
    """Cloud voice first, local voice when offline. Mirrors FailoverLLM."""

    def __init__(self, chain: list[TTS]):
        if not chain:
            raise RuntimeError("No TTS backend configured")
        self.chain = chain
        self.last_used = "-"

    @property
    def name(self) -> str:
        return self.last_used

    async def synth(self, text: str) -> Audio:
        errors = []
        for tts in self.chain:
            try:
                out = await tts.synth(text)
                self.last_used = tts.name
                return out
            except Exception as e:
                log.warning("tts %s failed: %s", tts.name, e)
                errors.append(f"{tts.name}: {e}")
        raise RuntimeError("all TTS failed -> " + " | ".join(errors))


def available_backends() -> dict[str, TTS]:
    """Everything configured on this machine, keyed by name. Missing creds = silently skipped."""
    out: dict[str, TTS] = {}
    if k := os.getenv("OPENAI_API_KEY"):
        out["openai"] = OpenAITTS(k, os.getenv("ENKI_OPENAI_VOICE", "onyx"))
    if k := os.getenv("GOOGLE_TTS_API_KEY"):
        out["google"] = GoogleTTS(k, os.getenv("ENKI_GOOGLE_VOICE", "tr-TR-Chirp3-HD-Charon"))
    if (k := os.getenv("AZURE_SPEECH_KEY")) and (reg := os.getenv("AZURE_SPEECH_REGION")):
        out["azure"] = AzureTTS(k, reg, os.getenv("ENKI_AZURE_VOICE", "tr-TR-AhmetNeural"))
    if (m := os.getenv("ENKI_PIPER_MODEL")) and os.path.exists(m):
        try:
            out["piper"] = PiperTTS(m)
        except ImportError:
            log.warning("ENKI_PIPER_MODEL set but piper-tts not installed")
    try:
        rate, pitch = os.getenv("ENKI_EDGE_RATE", "+0%"), os.getenv("ENKI_EDGE_PITCH", "+0Hz")
        for v in ("tr-TR-AhmetNeural", "tr-TR-EmelNeural"):
            e = EdgeTTS(v, rate, pitch)
            out[e.name] = e
    except ImportError:
        pass
    if os.getenv("ENKI_SAY_VOICE") and shutil.which("say"):
        out["say"] = MacSayTTS(os.getenv("ENKI_SAY_VOICE", "Yelda"))
    return out


def build_tts(order: str | None = None) -> FailoverTTS:
    """ENKI_TTS='openai,piper' -> try openai, fall back to piper."""
    backends = available_backends()
    order = order or os.getenv("ENKI_TTS", "openai,google,azure,piper")
    chain = [backends[n] for n in (s.strip() for s in order.split(",")) if n in backends]
    return FailoverTTS(chain)


# ---------------- playback ----------------
async def play(audio: Audio) -> None:
    player = shutil.which("afplay") or shutil.which("aplay")  # macOS | Linux/Pi
    if not player:
        raise RuntimeError("no audio player (afplay/aplay) found")
    with tempfile.NamedTemporaryFile(suffix=f".{audio.ext}", delete=False) as f:
        f.write(audio.data)
        path = f.name
    try:
        p = await asyncio.create_subprocess_exec(player, path)
        await p.wait()
    finally:
        os.unlink(path)


class VoiceSpeaker:
    """Brain's Speaker port: print + speak. Text still prints so the console stays usable."""

    def __init__(self, tts: TTS, printer):
        self.tts, self.printer = tts, printer

    async def say(self, text: str) -> None:
        await self.printer.say(text)
        try:
            await play(await self.tts.synth(text))
        except Exception as e:
            log.warning("voice failed, text only: %s", e)
