"""Scene-change detection: EMA background model + frame differencing, with hysteresis."""
from __future__ import annotations

import cv2
import numpy as np

from .events import Event, EventBus, EventType


class MotionDetector:
    """B_t = (1-α)·B_{t-1} + α·I_t ; mask = |I_t - B_t| > τ ; score = |mask| / |pixels|

    All buffers are preallocated once at first frame — zero per-frame heap allocs
    in the hot path (matters on a Pi, where GC/alloc jitter shows up as dropped frames).
    """

    def __init__(
        self,
        bus: EventBus,
        work_width: int = 320,
        alpha: float = 0.04,
        pixel_thresh: int = 25,
        on_ratio: float = 0.012,
        off_ratio: float = 0.004,
        off_frames: int = 12,
        lighting_ratio: float = 0.55,
        min_blob_area: int = 60,
    ) -> None:
        self.bus = bus
        self.work_width, self.alpha, self.pixel_thresh = work_width, alpha, pixel_thresh
        self.on_ratio, self.off_ratio, self.off_frames = on_ratio, off_ratio, off_frames
        self.lighting_ratio, self.min_blob_area = lighting_ratio, min_blob_area
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self._bg: np.ndarray | None = None
        self.active = False
        self._quiet = 0
        self.score = 0.0
        self.boxes: list[tuple[int, int, int, int]] = []

    def _alloc(self, frame: np.ndarray) -> None:
        h, w = frame.shape[:2]
        self._scale = self.work_width / w
        self._size = (self.work_width, round(h * self._scale))
        sw, sh = self._size
        self._small = np.empty((sh, sw, 3), np.uint8)
        self._gray = np.empty((sh, sw), np.uint8)
        self._bg_u8 = np.empty((sh, sw), np.uint8)
        self._diff = np.empty((sh, sw), np.uint8)
        self._mask = np.empty((sh, sw), np.uint8)
        self._prep(frame)
        self._bg = self._gray.astype(np.float32)

    def _prep(self, frame: np.ndarray) -> None:
        cv2.resize(frame, self._size, dst=self._small, interpolation=cv2.INTER_AREA)
        cv2.cvtColor(self._small, cv2.COLOR_BGR2GRAY, dst=self._gray)
        cv2.GaussianBlur(self._gray, (7, 7), 0, dst=self._gray)

    def update(self, frame: np.ndarray) -> float:
        if self._bg is None:
            self._alloc(frame)
            return 0.0
        self._prep(frame)
        cv2.convertScaleAbs(self._bg, dst=self._bg_u8)
        cv2.absdiff(self._gray, self._bg_u8, dst=self._diff)
        cv2.threshold(self._diff, self.pixel_thresh, 255, cv2.THRESH_BINARY, dst=self._mask)
        raw = cv2.countNonZero(self._mask) / self._mask.size
        cv2.dilate(self._mask, self._kernel, dst=self._mask, iterations=2)
        self.score = cv2.countNonZero(self._mask) / self._mask.size

        if raw > self.lighting_ratio and self._is_global_shift(raw):
            self._bg[:] = self._gray
            self.bus.publish(Event(EventType.LIGHTING_CHANGE, {"score": round(self.score, 3)}))
            self.score, self.boxes = 0.0, []
            return 0.0

        # Freeze background under moving regions -> slow movers don't get absorbed instantly
        cv2.accumulateWeighted(self._gray, self._bg, self.alpha, mask=cv2.bitwise_not(self._mask))
        cv2.accumulateWeighted(self._gray, self._bg, self.alpha * 0.1, mask=self._mask)

        self._extract_boxes()
        self._hysteresis()
        return self.score

    def _is_global_shift(self, raw: float) -> bool:
        """Lights/auto-exposure = additive offset over the whole image. Remove the mean offset
        and re-diff: if most of the 'change' vanishes, it was illumination. A face close to the
        lens also changes >50% of pixels, but its change is structural and survives the offset removal.
        Cold path (only on huge changes) -> allocation here is acceptable.
        """
        offset = float(self._gray.mean()) - float(self._bg.mean())
        residual = cv2.absdiff(self._gray.astype(np.float32) - offset, self._bg)
        return float((residual > self.pixel_thresh).mean()) < 0.5 * raw

    def _extract_boxes(self) -> None:
        inv = 1.0 / self._scale
        contours, _ = cv2.findContours(self._mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        self.boxes = [
            tuple(int(v * inv) for v in cv2.boundingRect(c))
            for c in contours
            if cv2.contourArea(c) >= self.min_blob_area
        ]

    def _hysteresis(self) -> None:
        if not self.active and self.score > self.on_ratio:
            self.active, self._quiet = True, 0
            self.bus.publish(Event(EventType.MOTION_START, {"score": round(self.score, 3)}))
        elif self.active:
            self._quiet = self._quiet + 1 if self.score < self.off_ratio else 0
            if self._quiet >= self.off_frames:
                self.active = False
                self.bus.publish(Event(EventType.MOTION_END))
