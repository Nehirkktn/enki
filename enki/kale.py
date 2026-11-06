"""Kale: Mac'in başında yabancı varken bilgisayarı kilitler, Nehir'in yüzünü görünce açar.

Karar (KilitKarari, saf, test edilebilir):
  kilitle ← kamera Nehir'i görmüyor + tanımadığı biri var, KILIT_S sn kesintisiz
  aç      ← kamera Nehir'i yüksek benzerlikle görüyor (ACMA_ESIK, normal tanımadan sıkı), ACMA_S sn
Kilit ayrı bir süreçtir (enki/kilit.py): Enki çökse ya da öldürülse bile kilit kalır.
Elle açmak: kilit ekranında Enki şifresini yazıp Enter. Şifreyle açınca 10 dk yeniden kilitlemez
(kamera seni göremiyor olabilir: karanlık, açı...).
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger("enki.kale")
ROOT = Path(__file__).resolve().parents[1]
PID = ROOT / "data" / "kilit.pid"
AC = ROOT / "data" / "kilit.ac"

KILIT_S = float(os.getenv("ENKI_KILIT_SURE", "4"))
ACMA_S = 1.0
ACMA_ESIK = float(os.getenv("ENKI_KILIT_ESIK", "0.50"))  # tanıma eşiği 0.36; senin skorların ~0.6–0.86
SIFRE_MOLASI_S = 600


class KilitKarari:
    """Durumsuz sensör okumalarını zaman histerezisiyle karara çevirir. clock dışarıdan → test edilebilir."""

    def __init__(self, kilit_s: float = KILIT_S, acma_s: float = ACMA_S):
        self.kilit_s, self.acma_s = kilit_s, acma_s
        self._yabanci_t: float | None = None
        self._sahip_t: float | None = None
        self.mola_until = 0.0

    def update(self, now: float, sahip: bool, yabanci: bool, kilitli: bool) -> str | None:
        self._sahip_t = (now if self._sahip_t is None else self._sahip_t) if sahip else None
        self._yabanci_t = (now if self._yabanci_t is None else self._yabanci_t) if (yabanci and not sahip) else None
        if kilitli:
            if self._sahip_t is not None and now - self._sahip_t >= self.acma_s:
                return "ac"
            return None
        if now < self.mola_until:
            return None
        if self._yabanci_t is not None and now - self._yabanci_t >= self.kilit_s:
            self._yabanci_t = None
            return "kilitle"
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _is_kilit(pid: int) -> bool:
    try:
        cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
    except OSError:
        return True
    return "enki.kilit" in cmd


def helper_pid() -> int | None:
    try:
        pid = int(PID.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    if _alive(pid) and _is_kilit(pid):  # PID yeniden kullanılmış olabilir (Mac yeniden açıldı): gerçekten kilit mi?
        return pid
    PID.unlink(missing_ok=True)
    return None


class Kale:
    def __init__(self, vision, on_kilit: Callable[[], None], on_acik: Callable[[str], None]):
        self.vision, self.on_kilit, self.on_acik = vision, on_kilit, on_acik
        self.karar = KilitKarari()
        self._biz_actik = False
        self._proc: subprocess.Popen | None = None
        self._task = None

    @property
    def kilitli(self) -> bool:
        if self._proc is not None:  # kendi çocuğumuz: os.kill(pid, 0) zombiyi de "canlı" sayar, poll() saymaz
            if self._proc.poll() is None:
                return True
            PID.unlink(missing_ok=True)
            return False
        return helper_pid() is not None

    def start(self, loop: asyncio.AbstractEventLoop) -> "Kale":
        if self.kilitli:
            log.info("Enki açıldığında kilit zaten açıktı (önceki kopyadan): yüzünü görünce açacağım")
        self._task = loop.create_task(self._run())
        return self

    def sensors(self) -> tuple[bool, bool]:
        """(sahip: Nehir net görünüyor, yabanci: başka biri var). Kamera yoksa ikisi de False → karar yok."""
        v = self.vision
        p = getattr(v, "percept", None)
        if not v.ok or p is None:
            return False, False
        from .senses.perception.events import Presence
        sahip = p.state is Presence.OWNER and (p.sim_ema or 0) >= ACMA_ESIK
        yabanci = v.others > 0 and p.state is not Presence.OWNER
        return sahip, yabanci

    async def _run(self) -> None:
        onceki = self.kilitli
        while True:
            await asyncio.sleep(0.25)
            try:
                kilitli = self.kilitli
                if onceki and not kilitli:          # kilit kalktı
                    rc = self._proc.poll() if self._proc else 0
                    if rc not in (0, None):          # kilit süreci çöktü (izin? paket?) → döngüye girme
                        self.karar.mola_until = time.monotonic() + 60
                        log.error("kilit çalışmadı (çıkış %s): data/kilit.log'a bak", rc)
                    elif not self._biz_actik:        # biz açmadık → ekranda şifre yazıldı
                        self.karar.mola_until = time.monotonic() + SIFRE_MOLASI_S
                        log.info("kilit şifreyle açıldı: %d dk yeniden kilitlemem", SIFRE_MOLASI_S // 60)
                        self.on_acik("sifre")
                    self._biz_actik, self._proc = False, None
                onceki = kilitli
                sahip, yabanci = self.sensors()
                match self.karar.update(time.monotonic(), sahip, yabanci, kilitli):
                    case "kilitle":
                        self.kilitle()
                        onceki = True
                    case "ac":
                        self.ac()
            except Exception:
                log.exception("kale")

    def kilitle(self) -> None:
        if self.kilitli:
            return
        AC.unlink(missing_ok=True)
        token = secrets.token_hex(8)
        logf = open(ROOT / "data" / "kilit.log", "a")
        p = subprocess.Popen([sys.executable, "-m", "enki.kilit", token], cwd=ROOT,
                             stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
                             start_new_session=True)  # Enki'nin süreç grubundan ayrı: Enki ölse de yaşar
        PID.write_text(f"{p.pid} {token}")
        self._proc = p
        log.info("KİLİTLENDİ (yabancı var, sen yoksun) pid=%d", p.pid)
        self.on_kilit()

    def ac(self, neden: str = "yuz") -> None:
        try:
            token = PID.read_text().split()[1]
        except (OSError, IndexError):
            return
        self._biz_actik = True
        AC.write_text(token)
        log.info("kilit açılıyor (%s)", neden)
        self.on_acik(neden)
