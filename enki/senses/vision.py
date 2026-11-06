"""Göz adapter'ı: kamera → PerceptionPipeline → beynin olayları (FaceSeen / FaceLeft).

Kendi thread'inde döner; beyne sadece `post` (thread-safe) üzerinden konuşur.
Bakış geçmişini de tutar: kulak "bana mı konuşuyor?" kararını buna göre verir.
"""
from __future__ import annotations

import collections
import logging
import threading
import time
from pathlib import Path
from typing import Callable

from ..brain import Event, FaceLeft, FaceSeen
from .perception.events import Event as PEvent, EventType

log = logging.getLogger("enki.vision")
COMPANY_S = 3.0


def to_brain_event(ev: PEvent, body: str) -> Event | None:
    """Algı olayı → beyin olayı. Saf fonksiyon (test edilebilir, kamera gerektirmez)."""
    match ev.type:
        case EventType.OWNER_ARRIVED:
            return FaceSeen("nehir", body)
        case EventType.STRANGER_APPEARED:
            return FaceSeen("yabanci", body)
        case EventType.OWNER_LEFT:
            return FaceLeft("nehir", body)
    return None


class VisionSense:
    def __init__(self, post: Callable[[Event], None], root: Path, camera: int | str = 0,
                 threshold: float = 0.363, body: str = "mac"):
        self.post, self.root, self.camera, self.threshold, self.body = post, root, camera, threshold, body
        self._gaze: collections.deque[tuple[float, bool]] = collections.deque(maxlen=900)  # ~30 s
        self._stop = threading.Event()
        self.frame = None
        self.percept = None
        self.ok = False
        self.others = 0            # Nehir dışında, en az COMPANY_S sn görünen yüz sayısı
        self._cand_others, self._cand_t = 0, 0.0

    def start(self) -> "VisionSense":
        threading.Thread(target=self._run, name="enki-vision", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def gaze_fraction(self, t0: float, t1: float) -> float:
        xs = [f for t, f in list(self._gaze) if t0 <= t <= t1]
        return sum(xs) / len(xs) if xs else 0.0

    def snapshot_jpeg(self, width: int = 512, quality: int = 70) -> bytes | None:
        """Anlık kare, 512 px ≈ 350 görüntü token'ı: kıyafet/eşya seçilir, gecikme düşük kalır."""
        f = self.frame
        if f is None or not self.ok:
            return None
        import cv2
        h, w = f.shape[:2]
        small = cv2.resize(f, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None

    def _track_company(self, n: int, now: float) -> None:
        """Başka yüz sayısı COMPANY_S sn sabit kalınca kabul edilir: başını çevirdiğin 1-2 sn'lik
        'yabancı' titremeleri (benzerlik anlık düşüyor) misafir sayılmasın."""
        n = max(0, n)
        if n != self._cand_others:
            self._cand_others, self._cand_t = n, now
        elif n != self.others and now - self._cand_t >= COMPANY_S:
            log.info("yanında başka %d kişi var", n) if n else log.info("misafir gitti")
            self.others = n

    def _on_event(self, ev: PEvent) -> None:
        log.info("%s %s", ev.type.name, ev.data)
        if (bev := to_brain_event(ev, self.body)) is not None:
            self.post(bev)

    def _run(self) -> None:
        try:
            from .perception.camera import Camera
            from .perception.events import EventBus, Presence
            from .perception.pipeline import PerceptionPipeline

            bus = EventBus()
            bus.subscribe(self._on_event)
            pipe = PerceptionPipeline(bus, self.root, threshold=self.threshold)
            if not len(pipe.gallery):
                log.warning("yüz kaydı yok (data/owner.npz) → herkes yabancı. `enki kayit` ile öğret.")
            with Camera(self.camera) as cam:
                self.ok, seq = True, -1
                log.info("kamera açık")
                while not self._stop.is_set():
                    seq, frame = cam.read(seq)
                    p = pipe.process(frame)
                    self.frame, self.percept = frame, p
                    now = time.monotonic()
                    self._gaze.append((now, p.facing and p.state is Presence.OWNER))
                    if p.detected_this_tick:
                        self._track_company(len(p.faces) - (1 if p.state is Presence.OWNER else 0), now)
        except Exception as e:
            self.ok = False
            log.exception("göz kapalı: %s", e)


POZLAR = ["düz kameraya bak", "başını yavaşça sağa çevir", "başını yavaşça sola çevir",
          "hafif yukarı bak", "hafif aşağı bak (klavyeye bakar gibi)", "gülümse / konuşur gibi yap",
          "gözlük/saç farklıysa şimdi değiştir", "ekrana yaklaş, sonra biraz uzaklaş"]


def enroll(root: Path, camera: int | str = 0, samples: int = 32, reset: bool = False) -> int:
    """Nehir'in yüzünü öğret. Mevcut kayda EKLER (--sifirla ile sıfırdan). Pozları ekranda söyler.
    Aynı poza ait neredeyse aynı örnekleri galeri kendisi reddeder: çeşitlilik = farklı açı ve ışık."""
    from .perception.camera import Camera
    from .perception.events import EventBus
    from .perception.faces import largest
    from .perception.pipeline import PerceptionPipeline

    pipe = PerceptionPipeline(EventBus(), root)
    if reset:
        pipe.gallery.G = pipe.gallery.G[:0]
    last, seq = 0.0, -1
    start = len(pipe.gallery)
    target = start + samples
    per = max(1, samples // len(POZLAR))
    print(f"Mevcut örnek: {start}. {samples} yeni örnek eklenecek. Tek kişi kadrajda olsun.")
    print("İpucu: bitince ışığı değiştirip (tavan lambası / masa lambası) bir kez daha çalıştır.")
    shown = -1
    with Camera(camera) as cam:
        while len(pipe.gallery) < target:
            k = min((len(pipe.gallery) - start) // per, len(POZLAR) - 1)
            if k != shown:
                print(f"\n→ {POZLAR[k]}")
                shown = k
            seq, frame = cam.read(seq)
            faces = pipe.detector.detect(frame)
            f = largest(faces)
            if len(faces) == 1 and f[14] >= 0.9 and time.time() - last > 0.25:
                if pipe.gallery.try_add(pipe.embedder.embed(frame, f)):
                    print(f"\r  {len(pipe.gallery) - start}/{samples}", end="", flush=True)
                last = time.time()
    pipe.gallery.save()
    print(f"\nKaydedildi → {pipe.gallery.path}")
    return len(pipe.gallery)
