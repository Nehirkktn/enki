"""Kulak adapter'ı: mikrofon → VAD → Whisper → UserSaid (bana konuşuyor) / Overheard (kendi kendine).

"Bana mı konuşuyor?" kararı (öncelik sırasıyla):
  1) cümlede "Enki" geçiyor         2) Enki az önce konuştu (sohbet devam ediyor)
  3) konuşurken kameraya bakıyordu (bakış oranı ≥ eşik)
Enki konuşurken mikrofon susturulur (`muted`), yoksa kendi sesine cevap verir.
"""
from __future__ import annotations

import logging
import platform
import re
import threading
import time
from typing import Callable

from ..brain import Event, Overheard, UserSaid

log = logging.getLogger("enki.ear")

# Whisper'ın hiç görmediği "Enki" kelimesini yazma biçimleri (Enki = NK = Nehir Kökten)
WAKE = re.compile(r"\b(?:enk[iıy]+|en k[iı]|eng[iı]|enkey|enke|enka|en ka|nk|n\.\s?k\.?)(?!\w)")


def tr_lower(s: str) -> str:
    return s.replace("İ", "i").replace("I", "ı").lower()


def addressed(text: str, t0: float, t1: float, now: float, last_spoke: float,
              gaze: Callable[[float, float], float] | None,
              follow_up_s: float = 10.0, gaze_ratio: float = 0.4) -> tuple[bool, str]:
    if WAKE.search(tr_lower(text)):
        return True, "isim"
    if now - last_spoke < follow_up_s:
        return True, "sohbet devam"
    g = gaze(t0, t1) if gaze else 0.0
    return (g >= gaze_ratio), f"bakış {g:.0%}"


class EarSense:
    def __init__(self, post: Callable[[Event], None], muted: threading.Event,
                 last_spoke: Callable[[], float], gaze: Callable[[float, float], float] | None = None,
                 whisper: str = "auto", body: str = "mac"):
        self.post, self.muted, self.last_spoke, self.gaze, self.body = post, muted, last_spoke, gaze, body
        if whisper == "auto":
            whisper = "turbo" if platform.system() == "Darwin" and platform.machine() == "arm64" else "small"
        self.whisper = whisper
        self.ok = False

    def start(self) -> "EarSense":
        from .listener import Listener
        from .stt import Transcriber

        self.stt = Transcriber(self.whisper)        # ilk seferde model iner; hata burada yukarı çıkar
        self.listener = Listener(mute_while=self.muted).start()
        self.ok = True
        threading.Thread(target=self._run, name="enki-ear", daemon=True).start()
        return self

    @property
    def hearing(self) -> bool:
        return self.ok and self.listener.hearing

    def _run(self) -> None:
        while True:
            utt = self.listener.out.get()
            try:
                text = self.stt(utt.audio, hotwords="Enki")
            except Exception:
                log.exception("whisper hata")
                continue
            if not text:
                continue
            ok, why = addressed(text, utt.t_start, utt.t_end, time.monotonic(), self.last_spoke(), self.gaze)
            log.info("%s (%s): %s", "bana" if ok else "kendi kendine", why, text)
            self.post(UserSaid(text, self.body) if ok else Overheard(text, self.body))
