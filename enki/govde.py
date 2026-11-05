"""Beynin Speaker portu, çok gövdeli: cevap Nehir'in konuştuğu gövdeden çıkar, gözler her yerde aynı duyguyu gösterir.

  konsol / mac  → Mac hoparlörü (EnkiVoice)       telefon → ses telefonda üretilmez: Mac'te render edilir,
  gözler        → hub.face(duygu, altyazı)                  WAV olarak telefona gider (Enki'nin aynı sesi)
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from typing import Callable

from .fsm import Q, Sigma
from .tts import SR, strip_tags

log = logging.getLogger("enki.govde")

# cevap etiketi → yüz durumu (enki-yuz.html'in bildiği adlar)
FACE = {"blush": "blush", "smug": "smug", "stern": "angry", "excited": "proud", "laugh": "laugh",
        "kahkaha": "laugh", "laughbig": "laugh", "laugh_big": "laugh", "angry": "angry", "love": "love",
        "surprised": "surprised", "sad": "sad", "confused": "confused", "proud": "proud", "bored": "bored"}
_TAG = re.compile(r"\[([A-Za-z_]+)\]")
LOCAL_BODIES = {"konsol", "mac", "yuz"}

# FSM durumu → yüz (SPEAKING'i konuşmacı kendisi, duygu etiketiyle gösterir)
FSM_FACE = {Q.LISTENING: "listening", Q.THINKING: "thinking", Q.SLEEPING: "sleep", Q.IDLE: "idle"}


def face_emotion(text: str) -> str:
    """İlk tanınan etiket gözlerin duygusu olur; yoksa normal konuşma."""
    for m in _TAG.finditer(text):
        if (f := FACE.get(m.group(1).lower())):
            return f
    return "speaking"


def fsm_face(prev: Q, s: Sigma, nxt: Q) -> str | None:
    if nxt is Q.SPEAKING or (nxt is Q.IDLE and s is Sigma.DONE_SPEAKING):
        return None  # BodySpeaker yönetiyor
    return FSM_FACE.get(nxt)


class BodySpeaker:
    def __init__(self, hub, local, printer, active: Callable[[], str], voice=None,
                 muted: threading.Event | None = None):
        self.hub, self.local, self.printer, self.active, self.voice = hub, local, printer, active, voice
        self.muted = muted or threading.Event()
        self.last_spoke = 0.0

    async def say(self, text: str) -> None:
        clean = strip_tags(text)
        body = self.active()
        if self.hub:
            await self.hub.face(face_emotion(text), clean)
        self.muted.set()  # kulak kendi sesini duymasın
        try:
            if self.hub and body not in LOCAL_BODIES and self.hub.has(body, "speaker"):
                await self.printer.say(text)
                await self._remote(body, text, clean)
            else:
                await self.local.say(text)
        finally:
            self.muted.clear()
            self.last_spoke = time.monotonic()
            if self.hub:
                await self.hub.face("idle")

    async def _remote(self, body: str, text: str, clean: str) -> None:
        from .hub import wav_b64

        msg: dict = {"say": clean}
        pcm = None
        if self.voice:
            try:
                pcm = await self.voice.render(text)
            except Exception as e:
                log.warning("telefon için ses üretilemedi: %s", e)
        if pcm is not None:
            msg["audio"] = wav_b64(pcm, SR)
        await self.hub.send_to(body, msg)
        if pcm is not None:  # FSM dürüst kalsın: telefon çalarken Enki "konuşuyor"
            await asyncio.sleep(len(pcm) / SR)
