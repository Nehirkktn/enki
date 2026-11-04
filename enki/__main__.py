"""Enki konsolu. Varsayılan: yazarak konuşursun, Enki hem yazar hem sesli cevap verir.

  enki                      # sesli (Ahmet + TON + JARVIS + gülme kayıtları)
  enki --sessiz             # sadece yazı (ya da ENKI_SESSIZ=1)
  /kendi <metin>            # kendi kendine konuşma simülasyonu (Overheard)
  /yuz                      # kamera seni gördü (FaceSeen)
  /hafiza                   # Enki ne biliyor
  /cik
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from .brain import Brain, FaceSeen, Overheard, Shutdown, UserSaid
from .config import Config
from .fsm import Q
from .llm import build_llm
from .memory import Memory
from .tts import strip_tags

DIM, CYAN, YELLOW, RESET = "\033[2m", "\033[36m", "\033[33m", "\033[0m"


class ConsoleSpeaker:
    """Prefixes each reply with the brain that produced it; fallback is yellow so it can't hide."""

    def __init__(self, brain_name=lambda: ""):
        self.brain_name = brain_name

    async def say(self, text: str) -> None:
        name = self.brain_name()
        tag = f" {DIM if name == 'claude' else YELLOW}[{name}]{RESET}{CYAN}" if name else ""
        print(f"\r{CYAN}Enki{tag} ›{RESET} {strip_tags(text)}\n{DIM}sen ›{RESET} ", end="", flush=True)


async def stdin_loop(brain: Brain, mem: Memory) -> None:
    loop = asyncio.get_running_loop()
    print(f"{DIM}sen ›{RESET} ", end="", flush=True)
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            brain.post(Shutdown())
            return
        line = line.strip()
        if not line:
            continue
        if line == "/cik":
            brain.post(Shutdown())
            return
        if line == "/hafiza":
            print("\n".join(f"  • {f}" for f in mem.facts()) or "  (boş)")
        elif line == "/yuz":
            brain.post(FaceSeen())
        elif line.startswith("/kendi "):
            brain.post(Overheard(line[7:]))
        else:
            brain.post(UserSaid(line))


def build_speaker(llm, silent: bool):
    console = ConsoleSpeaker(lambda: llm.name)
    if silent:
        return console
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        print(f"{YELLOW}ses kapalı: edge-tts kurulu değil (pip install edge-tts){RESET}")
        return console
    from .tts import EnkiVoice, VoiceSpeaker

    return VoiceSpeaker(EnkiVoice(), console)


async def amain(silent: bool) -> None:
    logging.basicConfig(level=os.getenv("ENKI_LOG", "WARNING"), format="%(name)s: %(message)s")
    cfg = Config()
    mem = Memory(cfg)
    llm = build_llm(cfg)
    brain = Brain(cfg, llm, mem, build_speaker(llm, silent))

    if os.getenv("ENKI_DEBUG_FSM"):
        brain.fsm.subscribe(lambda a, s, b: print(f"{DIM}[{a.name} --{s.name}--> {b.name}]{RESET}"))
    brain.fsm.subscribe(  # eyes hook goes here in Phase 1.4
        lambda a, s, b: b is Q.THINKING and print(f"\r{DIM}…{RESET}", end="", flush=True)
    )

    reader = asyncio.create_task(stdin_loop(brain, mem))
    try:
        await brain.run()
    finally:
        reader.cancel()
        mem.close()


def main() -> None:
    ap = argparse.ArgumentParser(prog="enki")
    ap.add_argument("--sessiz", action="store_true", help="sadece yazı, ses yok")
    args = ap.parse_args()
    try:
        asyncio.run(amain(args.sessiz or bool(os.getenv("ENKI_SESSIZ"))))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
