"""Orchestrator. Single-consumer event loop: every input (speech, overheard speech, camera,
clock ticks) is an Event in one asyncio.Queue. One consumer => no locks, no races on the FSM
or memory. I/O adapters (Whisper, Piper, OpenCV) only *produce* events or *consume* text.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from . import persona
from .config import Config
from .fsm import Q, Sigma, StateMachine
from .initiative import Initiative
from .llm import LLM, Message
from .memory import Memory

log = logging.getLogger("enki.brain")


# ---------- events ----------
# `body`: which body the event came from (konsol, mac, telefon, oda-1...). One mind, many bodies.
@dataclass(frozen=True, slots=True)
class UserSaid:
    text: str
    body: str = "konsol"


@dataclass(frozen=True, slots=True)
class Overheard:  # speech without the wake word: she's talking to herself
    text: str
    body: str = "konsol"


@dataclass(frozen=True, slots=True)
class FaceSeen:
    who: str = "nehir"
    body: str = "mac"


@dataclass(frozen=True, slots=True)
class FaceLeft:
    who: str = "nehir"
    body: str = "mac"


@dataclass(frozen=True, slots=True)
class Tick:
    pass


@dataclass(frozen=True, slots=True)
class Shutdown:
    pass


Event = UserSaid | Overheard | FaceSeen | FaceLeft | Tick | Shutdown


class Speaker(Protocol):
    async def say(self, text: str) -> None: ...


# ---------- reply parsing ----------
_MEM_RE = re.compile(r"<hafiza>(.*?)(?:</hafiza>|$)", re.S | re.I)
_SILENT = "[SUS]"
_MD_RE = re.compile(r"[*_#`>]+")
# Görmeyle ilgili soru → kameranın anlık karesi Claude'a gider (her mesajda değil: gecikme + maliyet)
VISUAL_RE = re.compile(r"\b(gör|bak[ıaı]|kamera|giy|elimde|saçım|yüzüm|suratım|odam|masam|arkam|ışı[kğ]|"
                       r"karanlık|ne yapıyorum|neredeyim|kıyafet|tişört|gözlük|kim var|yanımda)", re.I)


@dataclass(slots=True)
class Reply:
    speech: str
    facts: list[str]
    silent: bool


def parse_reply(raw: str) -> Reply:
    facts = [ln for m in _MEM_RE.findall(raw) for ln in m.splitlines() if ln.strip()]
    speech = _MEM_RE.sub("", raw)
    speech = _MD_RE.sub("", speech).strip()
    silent = speech.replace(_SILENT, "").strip() == "" or speech.startswith(_SILENT)
    return Reply("" if silent else speech.replace(_SILENT, "").strip(), facts, silent)


def to_messages(turns: list[tuple[str, str]], tail_user: str | None) -> list[Message]:
    """Anthropic needs: starts with user, strictly alternating. Merge runs, drop leading bot."""
    out: list[Message] = []
    for role, text in turns + ([("user", tail_user)] if tail_user else []):
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n" + text
        elif out or role == "user":
            out.append({"role": role, "content": text})
    if not out or out[-1]["role"] != "user":
        out.append({"role": "user", "content": "[devam et]"})
    return out


# ---------- brain ----------
class Brain:
    def __init__(self, cfg: Config, llm: LLM, memory: Memory, speaker: Speaker,
                 rng: random.Random | None = None, eyes=None):
        self.cfg, self.llm, self.mem, self.speaker = cfg, llm, memory, speaker
        self.eyes = eyes  # () -> jpeg bytes | None
        self.company = None  # () -> int: kamerada Nehir dışında kaç kişi var
        self.observers: list = []
        self.hands = None                 # eller.Hands | None
        self._confirm: asyncio.Future | None = None  # eller onay bekliyorsa: sıradaki UserSaid cevaptır  # her olayı izleyenler (ör. yüz ekranı: sahibi/yabancıyı görünce gözler)
        self.fsm = StateMachine()
        self.rng = rng or random.Random()
        self.initiative = Initiative(cfg, rng=self.rng)
        self.events: asyncio.Queue[Event] = asyncio.Queue()
        self._last_interject = 0.0
        self.active_body = "konsol"   # the body Nehir last spoke through; replies go there
        self.present: set[str] = set()  # bodies whose camera currently sees Nehir
        self._loop: asyncio.AbstractEventLoop | None = None

    def post(self, ev: Event) -> None:
        if self._confirm is not None and not self._confirm.done() and isinstance(ev, UserSaid) \
                and not ev.text.startswith("[MİSAFİR") and not (ev.body == "konsol" and self.stranger_at_mac()):
            self._confirm.set_result(ev.text)  # eller'in sorusuna cevap: kuyruğa girmez
            return
        self.events.put_nowait(ev)

    def post_threadsafe(self, ev: Event) -> None:
        """For sense threads (camera, mic). Safe to call before run(): queued until the loop exists."""
        if self._loop is None:
            self.events.put_nowait(ev)
        else:
            self._loop.call_soon_threadsafe(self.post, ev)

    def stranger_at_mac(self) -> bool:
        """Kamera Mac'in başında Nehir'i görmüyor ama tanımadığı birini görüyor."""
        return "mac" not in self.present and bool(self.company and self.company() > 0)

    async def ask_confirm(self, soru: str, timeout: float = 45.0) -> bool:
        """Eller onay istiyor: Enki sesli sorar, Nehir'in sıradaki cümlesi cevaptır."""
        from .eller import YES_RE
        self._confirm = asyncio.get_running_loop().create_future()
        await self.speaker.say(f"[stern] Onay lazım: {soru}. Yapayım mı?")
        try:
            ans = await asyncio.wait_for(self._confirm, timeout)
        except asyncio.TimeoutError:
            ans = ""
        finally:
            self._confirm = None
        ok = bool(YES_RE.search(ans))
        log.info("onay: %r → %s", ans, ok)
        return ok

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        ticker = asyncio.create_task(self._ticker())
        try:
            while True:
                ev = await self.events.get()
                if isinstance(ev, Shutdown):
                    return
                try:
                    await self._handle(ev)
                except Exception:
                    log.exception("event %r failed", ev)
                    self.fsm.reset()  # never leave the bot stuck in THINKING
        finally:
            ticker.cancel()

    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.tick_s)
            if self.events.empty():  # don't pile ticks behind slow LLM calls
                self.post(Tick())

    async def _handle(self, ev: Event) -> None:
        for fn in self.observers:
            try:
                fn(ev)
            except Exception:
                log.exception("observer")
        match ev:
            case UserSaid(text, body):
                if body == "konsol" and self.stranger_at_mac() and not text.startswith("[MİSAFİR"):
                    text = f"[MİSAFİR] {text}"  # klavyede yazan Nehir değil: kamera onu görmüyor, başkasını görüyor
                self.active_body = body
                self.initiative.on_user_spoke()
                self.fsm.fire(Sigma.WAKE)
                self.fsm.fire(Sigma.HEARD)
                await self._turn(user_text=text, store_as=text)
            case Overheard(text):
                self.mem.add_turn("user", f"(kendi kendine) {text}")
                if self.fsm.q is not Q.IDLE or not self._may_interject():
                    return
                self.fsm.fire(Sigma.INITIATE)
                await self._turn(user_text=f"[ARAYA_GIR] {text}", store_as=None)
            case FaceLeft(_, body):
                self.present.discard(body)
            case FaceSeen(who, body):
                if who != "nehir":
                    return
                self.present.add(body)
                if self.fsm.q is Q.IDLE and (reason := self.initiative.should_greet()):
                    await self._initiate(reason)
            case Tick():
                if self.fsm.q is Q.IDLE and (reason := self.initiative.should_speak()):
                    await self._initiate(reason)

    def _may_interject(self) -> bool:
        now = time.time()
        if now - self._last_interject < 60 or self.rng.random() > 0.35:
            return False
        self._last_interject = now
        return True

    async def _initiate(self, reason: str) -> None:
        self.fsm.fire(Sigma.INITIATE)
        prompt = persona.initiative_prompt(self.initiative.silence_min(), reason)
        if await self._turn(user_text=prompt, store_as=None):
            self.initiative.on_enki_initiated()

    async def _turn(self, user_text: str, store_as: str | None) -> bool:
        """THINKING -> (SPEAKING -> IDLE | IDLE). Returns True if Enki spoke."""
        h = self.cfg.history_turns
        recent = [(t.role, t.text) for t in self.mem.recent(h)]
        recalled = self.mem.recall(user_text, exclude_recent=h)
        if store_as is not None:
            self.mem.add_turn("user", store_as)

        context = persona.dynamic_context(self.mem.facts(), recalled, datetime.now(),
                                          body=self.active_body, present=sorted(self.present),
                                          others=self.company() if self.company else 0)
        msgs = to_messages(recent, user_text)
        if self.eyes and store_as is not None and VISUAL_RE.search(user_text):
            jpg = self.eyes()
            if jpg:
                import base64
                msgs[-1]["content"] = [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                 "data": base64.b64encode(jpg).decode()}},
                    {"type": "text", "text": "[KAMERA: şu anki görüntü ekte]\n" + msgs[-1]["content"]},
                ]
                log.info("kamera karesi eklendi (%d KB)", len(jpg) // 1024)
        t0 = time.monotonic()
        from .eller import wants_hands
        if self.hands and store_as is not None and wants_hands(user_text):
            try:
                raw = await self.hands.run(user_text, context)
            except Exception as e:
                log.exception("eller hata")
                raw = f"[confused] Ellerim takıldı: {type(e).__name__}. Bir daha dener misin?"
        else:
            raw = await self.llm.complete(persona.CORE, context, msgs)
        log.info("beyin %.1f sn (%s)", time.monotonic() - t0, getattr(self.llm, "name", "?"))
        reply = parse_reply(raw)

        for f in reply.facts:
            self.mem.add_fact(f)
        if reply.silent:
            self.fsm.fire(Sigma.SILENT)
            return False

        self.mem.add_turn("assistant", reply.speech)
        self.fsm.fire(Sigma.REPLY_READY)
        await self.speaker.say(reply.speech)
        self.fsm.fire(Sigma.DONE_SPEAKING)
        return True
