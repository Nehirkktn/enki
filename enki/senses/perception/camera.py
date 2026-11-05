"""Latest-frame camera. Producer thread drains the driver buffer; consumer always gets the newest frame."""
from __future__ import annotations

import threading
import time

import cv2
import numpy as np


class Camera:
    """Why a thread: cv2.VideoCapture keeps an internal FIFO. If processing is slower than
    capture, read() returns frames that are seconds old -> the robot reacts to the past.
    Here the grabber overwrites a single slot; stale frames are dropped, not queued.
    """

    def __init__(self, source: int | str = 0, width: int = 640, height: int = 480) -> None:
        self._cap = cv2.VideoCapture(source)
        if not self._cap.isOpened():
            raise RuntimeError(f"camera {source!r} could not be opened")
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._frame: np.ndarray | None = None
        self._seq = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)

    def __enter__(self) -> "Camera":
        self._thread.start()
        deadline = time.time() + 5
        while self._frame is None and time.time() < deadline:
            time.sleep(0.01)
        if self._frame is None:
            raise RuntimeError("camera opened but produced no frames")
        return self

    def __exit__(self, *_) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        self._cap.release()

    def _run(self) -> None:
        while not self._stop.is_set():
            ok, f = self._cap.read()
            if not ok:
                time.sleep(0.01)
                continue
            with self._lock:
                self._frame, self._seq = f, self._seq + 1

    def read(self, last_seq: int = -1) -> tuple[int, np.ndarray]:
        """Blocks until a frame newer than last_seq exists -> never processes the same frame twice."""
        while True:
            with self._lock:
                if self._seq != last_seq and self._frame is not None:
                    return self._seq, self._frame  # no copy: grabber swaps references, never mutates
            time.sleep(0.002)
