"""Text-mode harness. Mic/STT/TTS adapters will replace stdin/print without touching Brain.

  python -m enki            # konuş
  ENKI_TTS=openai,piper python -m enki   # sesli
  /kendi <metin>            # kendi kendine konuşma simülasyonu (Overheard)
  /yuz                      # kamera seni gördü (FaceSeen)
  /hafiza                   # Enki ne biliyor
  /cik
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

from .brain import Brain, FaceSeen, Overheard, Shutdown, UserSaid
from .config import Config
from .fsm import Q
from .llm import build_llm
from .memory import Memory

DIM, CYAN, YELLOW, RESET = "\033[2m", "\033[36m", "\033[33m", "\033[0m"


class ConsoleSpeaker:
    """Prefixes each reply with the brain that produced it; fallback is yellow so it can't hide."""

    def __init__(self, brain_name=lambda: ""):
        self.brain_name = brain_name

    async def say(self, text: str) -> None:
        name = self.brain_name()
        tag = f" {DIM if name == 'claude' else YELLOW}[{name}]{RESET}{CYAN}" if name else ""
        print(f"\r{CYAN}Enki{tag} ›{RESET} {text}\n{DIM}sen ›{RESET} ", end="", flush=True)


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


async def amain() -> None:
    logging.basicConfig(level=os.getenv("ENKI_LOG", "WARNING"), format="%(name)s: %(message)s")
    cfg = Config()
    mem = Memory(cfg)
    llm = build_llm(cfg)
    speaker = ConsoleSpeaker(lambda: llm.name)
    if os.getenv("ENKI_TTS"):  # e.g. ENKI_TTS=openai,piper  -> speak out loud
        from .tts import VoiceSpeaker, build_tts

        speaker = VoiceSpeaker(build_tts(), ConsoleSpeaker(lambda: llm.name))
    brain = Brain(cfg, llm, mem, speaker)

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
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
