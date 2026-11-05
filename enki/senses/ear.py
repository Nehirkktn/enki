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


TYPING_S = 90.0  # son klavye girişinden bu kadar sn sonrasına kadar "yazma modu"
CALL_S = 8.0     # sadece "Enki" denirse: bu kadar sn içindeki sonraki cümle ona söylenmiş sayılır
_ONLY_NAME = re.compile(r"^\W*(?:" + WAKE.pattern + r")\W*$")


def only_name(text: str) -> bool:
    """Tek başına "Enki" / "Enki?" — gerçek bir çağrı da olabilir, Whisper'ın gürültüde uydurması da.
    Cevap üretmeyiz: Enki sadece dikkat kesilir, asıl cümleyi bekler."""
    return bool(_ONLY_NAME.match(tr_lower(text).strip()))


def addressed(text: str, t0: float, t1: float, now: float, last_spoke: float,
              gaze: Callable[[float, float], float] | None,
              follow_up_s: float = 10.0, gaze_ratio: float = 0.4,
              last_typed: float = -1e9) -> tuple[bool | None, str]:
    """True = bana konuşuyor, False = kendi kendine, None = yok say.
    Yazarken yüzü ekrana (kameraya) dönük: bakış kuralı her mırıltıyı Enki'ye sayıyordu.
    Yazma modunda sadece "Enki" denirse dinler, gerisini hiç duymamış sayar."""
    if WAKE.search(tr_lower(text)):
        return True, "isim"
    if now - last_typed < TYPING_S:
        return None, "yazıyor"
    if len(text.split()) < 2:
        return None, "tek kelime"  # gürültüden tek kelime uydurmalar ("Duyursun") sohbet/bakışla geçmesin
    if now - last_spoke < follow_up_s:
        return True, "sohbet devam"
    g = gaze(t0, t1) if gaze else 0.0
    return (g >= gaze_ratio), f"bakış {g:.0%}"


class EarSense:
    def __init__(self, post: Callable[[Event], None], muted: threading.Event,
                 last_spoke: Callable[[], float], gaze: Callable[[float, float], float] | None = None,
                 whisper: str = "auto", body: str = "mac", last_typed: Callable[[], float] = lambda: -1e9):
        self.post, self.muted, self.last_spoke, self.gaze, self.body = post, muted, last_spoke, gaze, body
        self.last_typed = last_typed
        self._called_at = -1e9
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
                # ipucu (initial_prompt) YOK: Whisper sessizlikte ipucunu kopyalayıp "Enki" uyduruyordu
                text = self.stt(utt.audio, hotwords=None)
            except Exception:
                log.exception("whisper hata")
                continue
            if not text:
                continue
            now = time.monotonic()
            if only_name(text):
                self._called_at = now
                log.info("çağrıldı, cümleyi bekliyor: %s", text)
                continue
            if now - self._called_at < CALL_S:
                self._called_at = -1e9
                ok, why = True, "çağrıdan sonra"
            else:
                ok, why = addressed(text, utt.t_start, utt.t_end, now, self.last_spoke(), self.gaze,
                                    last_typed=self.last_typed())
            log.info("%s (%s): %s", {True: "bana", False: "kendi kendine", None: "yok sayıldı"}[ok], why, text)
            if ok is None:
                continue
            self.post(UserSaid(text, self.body) if ok else Overheard(text, self.body))
