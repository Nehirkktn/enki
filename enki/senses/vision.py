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
        self.ok = False

    def start(self) -> "VisionSense":
        threading.Thread(target=self._run, name="enki-vision", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def gaze_fraction(self, t0: float, t1: float) -> float:
        xs = [f for t, f in list(self._gaze) if t0 <= t <= t1]
        return sum(xs) / len(xs) if xs else 0.0

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
                    self.frame = frame
                    self._gaze.append((time.monotonic(), p.facing and p.state is Presence.OWNER))
        except Exception as e:
            self.ok = False
            log.warning("göz kapalı: %s", e)


def enroll(root: Path, camera: int | str = 0, samples: int = 20, reset: bool = False) -> int:
    """Nehir'in yüzünü öğret: kameraya bak, başını yavaşça çevir. Pencere yok, terminalde sayar."""
    from .perception.camera import Camera
    from .perception.events import EventBus
    from .perception.faces import largest
    from .perception.pipeline import PerceptionPipeline

    pipe = PerceptionPipeline(EventBus(), root)
    if reset:
        pipe.gallery.G = pipe.gallery.G[:0]
    last, seq = 0.0, -1
    print(f"Kameraya bak, başını YAVAŞÇA sağa-sola-yukarı-aşağı çevir. Hedef: {samples}")
    with Camera(camera) as cam:
        while len(pipe.gallery) < samples:
            seq, frame = cam.read(seq)
            faces = pipe.detector.detect(frame)
            f = largest(faces)
            if len(faces) == 1 and f[14] >= 0.9 and time.time() - last > 0.25:
                if pipe.gallery.try_add(pipe.embedder.embed(frame, f)):
                    print(f"\r  {len(pipe.gallery)}/{samples}", end="", flush=True)
                last = time.time()
    pipe.gallery.save()
    print(f"\nKaydedildi → {pipe.gallery.path}")
    return len(pipe.gallery)
