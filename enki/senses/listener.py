"""Microphone -> VAD segmenter -> utterances. Cheap enough to run forever."""
from __future__ import annotations

import collections
import logging
import queue
import threading
import time
from dataclasses import dataclass

import numpy as np

log = logging.getLogger("enki.listener")

SR = 16_000
FRAME_MS = 30
FRAME = SR * FRAME_MS // 1000  # 480 samples; webrtcvad only accepts 10/20/30 ms frames


@dataclass(slots=True)
class Utterance:
    audio: np.ndarray  # float32 mono 16 kHz, [-1, 1]
    t_start: float     # time.monotonic()
    t_end: float


class Segmenter:
    """Pure state machine over VAD flags -> testable without a microphone.

    Start: ≥ start_ratio of the last `window` frames voiced (debounce: one cough isn't speech).
    End:   `end_ms` of continuous silence (a natural mid-sentence pause is ~300-500 ms).
    Pre-roll: frames before the trigger are kept, otherwise the first syllable is clipped.
    """

    def __init__(self, window: int = 10, start_ratio: float = 0.6, end_ms: int = 600,
                 max_s: float = 15.0, min_s: float = 0.5) -> None:
        self.window, self.start_ratio = window, start_ratio
        self.end_frames = end_ms // FRAME_MS
        self.max_frames = int(max_s * 1000 / FRAME_MS)
        self.min_frames = int(min_s * 1000 / FRAME_MS)
        self._ring: collections.deque[tuple[bytes, bool]] = collections.deque(maxlen=window)
        self._buf: list[bytes] = []
        self._silence = 0
        self._active = False
        self._t0 = 0.0

    def flush(self, now: float) -> Utterance | None:
        """Force-close an open utterance (e.g. recording window ended mid-sentence)."""
        if not self._active or len(self._buf) < self.min_frames:
            self.reset(); return None
        pcm = np.frombuffer(b"".join(self._buf), np.int16).astype(np.float32) / 32768.0
        u = Utterance(pcm, self._t0, now)
        self.reset()
        return u

    def reset(self) -> None:
        self._ring.clear()
        self._buf, self._silence, self._active = [], 0, False

    def push(self, frame: bytes, voiced: bool, now: float) -> Utterance | None:
        if not self._active:
            self._ring.append((frame, voiced))
            if len(self._ring) == self.window and sum(v for _, v in self._ring) >= self.start_ratio * self.window:
                self._active, self._silence = True, 0
                self._buf = [f for f, _ in self._ring]
                self._t0 = now - len(self._buf) * FRAME_MS / 1000
                self._ring.clear()
            return None
        self._buf.append(frame)
        self._silence = 0 if voiced else self._silence + 1
        if self._silence >= self.end_frames or len(self._buf) >= self.max_frames:
            buf, self._buf, self._active = self._buf, [], False
            if len(buf) - self._silence < self.min_frames:
                return None
            pcm = np.frombuffer(b"".join(buf), np.int16).astype(np.float32) / 32768.0
            return Utterance(pcm, self._t0, now)
        return None


class Listener:
    """Audio callback thread -> raw queue -> VAD thread -> utterance queue.

    The PortAudio callback must never block, so it only copies bytes into a queue.
    Half-duplex: while `mute_while` is set (robot speaking) + a short tail, input is
    discarded -- otherwise the robot hears itself and answers its own sentences.
    """

    def __init__(self, mute_while: threading.Event, aggressiveness: int = 2, tail_s: float = 0.4) -> None:
        import sounddevice as sd
        import webrtcvad
        self._sd = sd
        self._vad = webrtcvad.Vad(aggressiveness)  # 0..3, 3 = most aggressive at rejecting noise
        self.mute_while, self.tail_s = mute_while, tail_s
        self.out: queue.Queue[Utterance] = queue.Queue(maxsize=4)
        self.level = 0.0          # RMS for the HUD
        self.hearing = False      # true while inside an utterance
        self._raw: queue.Queue[bytes] = queue.Queue(maxsize=200)
        self._seg = Segmenter()
        self._muted_until = 0.0
        self._stream = None

    def start(self) -> "Listener":
        self._stream = self._sd.RawInputStream(samplerate=SR, blocksize=FRAME, channels=1,
                                               dtype="int16", callback=self._cb)
        self._stream.start()
        threading.Thread(target=self._loop, name="ear", daemon=True).start()
        log.info("mikrofon açık (%d Hz)", SR)
        return self

    def stop(self) -> None:
        if self._stream:
            self._stream.stop(); self._stream.close()

    def _cb(self, indata, frames, t, status) -> None:  # PortAudio thread: no blocking, no allocs beyond bytes()
        try:
            self._raw.put_nowait(bytes(indata))
        except queue.Full:
            pass

    def _loop(self) -> None:
        while True:
            frame = self._raw.get()
            now = time.monotonic()
            if self.mute_while.is_set():
                self._muted_until = now + self.tail_s
            if now < self._muted_until:
                self._seg.reset(); self.hearing = False
                continue
            if len(frame) != FRAME * 2:
                continue
            pcm = np.frombuffer(frame, np.int16)
            self.level = 0.8 * self.level + 0.2 * float(np.sqrt(np.mean((pcm / 32768.0) ** 2)))
            utt = self._seg.push(frame, self._vad.is_speech(frame, SR), now)
            self.hearing = self._seg._active
            if utt is not None:
                try:
                    self.out.put_nowait(utt)
                except queue.Full:
                    log.warning("utterance dropped (STT behind)")
