"""Composition root: wires detectors, smoothing and FSM. No camera/GUI here -> unit-testable on still frames."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Callable

import numpy as np

from .events import EventBus, Presence, PresenceFSM
from .faces import COSINE_THRESHOLD, SFACE, YUNET, FaceDetector, FaceEmbedder, OwnerGallery, ensure_model, largest
from .motion import MotionDetector


@dataclass(slots=True)
class Percept:
    """Snapshot of what the robot 'sees' this tick. The brain/UI reads this, never the detectors."""
    faces: np.ndarray = field(default_factory=lambda: np.empty((0, 15), np.float32))
    primary: np.ndarray | None = None
    sim: float | None = None      # raw cosine sim of primary face this tick
    sim_ema: float | None = None  # smoothed
    motion: float = 0.0
    motion_active: bool = False
    motion_boxes: list = field(default_factory=list)
    state: Presence = Presence.EMPTY
    detected_this_tick: bool = False
    yaw: float | None = None      # ~0 = looking at the camera, ± = head turned
    facing: bool = False


def head_yaw(face: np.ndarray) -> float:
    """Yaw proxy from 5 landmarks, no 3D model needed.

    YuNet landmarks: [re, le, nose, rm, lm]. When you face the camera the nose projects onto
    the midpoint of the eyes; turning your head shifts it sideways. Normalising by inter-ocular
    distance makes it scale-invariant (works at 40 cm and at 2 m):
        yaw ≈ (nose_x - (re_x + le_x)/2) / |le - re|
    Projective geometry: it's ~sin(θ)·(depth of nose / eye spacing), monotonic for |θ| < ~60°.
    """
    lm = face[4:14].reshape(5, 2)
    eye_mid = (lm[0] + lm[1]) / 2
    iod = float(np.linalg.norm(lm[1] - lm[0])) or 1.0
    return float((lm[2, 0] - eye_mid[0]) / iod)


class PerceptionPipeline:
    def __init__(
        self,
        bus: EventBus,
        root: Path,
        threshold: float = COSINE_THRESHOLD,
        ema_alpha: float = 0.35,
        idle_detect_every: int = 6,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        models = root / "models"
        self.bus = bus
        self.motion = MotionDetector(bus)
        self.detector = FaceDetector(ensure_model(models, YUNET))
        self.embedder = FaceEmbedder(ensure_model(models, SFACE))
        self.gallery = OwnerGallery(root / "data" / "owner.npz")
        self.fsm = PresenceFSM(bus, clock=clock)
        self.threshold, self.ema_alpha, self.idle_every = threshold, ema_alpha, idle_detect_every
        self._tick = 0
        self.facing_yaw = 0.22
        self.p = Percept()

    def process(self, frame: np.ndarray) -> Percept:
        p, self._tick = self.p, self._tick + 1
        p.motion = self.motion.update(frame)
        p.motion_active, p.motion_boxes = self.motion.active, self.motion.boxes

        # Motion-gated compute: scene static AND someone already confirmed -> sample faces less often.
        # Never gate when EMPTY, otherwise a person who walks in and freezes is never seen.
        static = not p.motion_active and self.fsm.state is not Presence.EMPTY
        p.detected_this_tick = not static or self._tick % self.idle_every == 0
        if not p.detected_this_tick:
            return p

        p.faces = self.detector.detect(frame)
        p.primary = largest(p.faces)
        p.yaw = head_yaw(p.primary) if p.primary is not None else None
        p.facing = p.yaw is not None and abs(p.yaw) < self.facing_yaw
        if p.primary is None:
            p.sim = p.sim_ema = None
            self.fsm.step(Presence.EMPTY)
        else:
            p.sim = self.gallery.similarity(self.embedder.embed(frame, p.primary))
            # Exponential smoothing = 1st-order IIR low-pass on a noisy per-frame score.
            p.sim_ema = p.sim if p.sim_ema is None else self.ema_alpha * p.sim + (1 - self.ema_alpha) * p.sim_ema
            self.fsm.step(
                Presence.OWNER if p.sim_ema >= self.threshold else Presence.STRANGER,
                sim=round(p.sim_ema, 3),
            )
        p.state = self.fsm.state
        return p
