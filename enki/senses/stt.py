"""Speech-to-text with faster-whisper (CTranslate2, int8, CPU). Same code runs on a Raspberry Pi."""
from __future__ import annotations

import logging
import platform
import re
import time

import numpy as np

log = logging.getLogger("enki.stt")

# Whisper was trained on subtitled video; on silence/noise it 'hallucinates' subtitle credits.
# These are the classic Turkish ones. Matching is on normalised text.
_JUNK = [
    "altyazı", "abone ol", "izlediğiniz için teşekkür", "teşekkürler.", "m.k.", "www.",
    "beğenmeyi unutmayın", "bir sonraki videoda", "hoşçakalın", "müzik",
]


def tr_lower(s: str) -> str:
    """str.lower() is locale-blind: 'İ'.lower() == 'i̇' (i + combining dot) and 'I' -> 'i' (should be 'ı')."""
    return s.replace("İ", "i").replace("I", "ı").lower()


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", tr_lower(s.strip()))


class Transcriber:
    """Two backends behind one call signature (Strategy):
      - mlx-whisper: Apple Silicon GPU (Metal). Several x faster on an M-series Mac.
      - faster-whisper: CTranslate2 int8 on CPU. Portable (Linux, Raspberry Pi).
    MLX is tried first on macOS/arm64; any import/load problem falls back to CPU."""

    MLX_REPOS = {"tiny": "mlx-community/whisper-tiny", "base": "mlx-community/whisper-base-mlx",
                 "small": "mlx-community/whisper-small-mlx", "medium": "mlx-community/whisper-medium-mlx",
                 "turbo": "mlx-community/whisper-large-v3-turbo"}

    def __init__(self, model: str = "small", language: str = "tr") -> None:
        self.language, self.backend = language, ""
        t0 = time.perf_counter()
        log.info("Whisper '%s' yükleniyor (ilk seferde indirilir)...", model)
        if platform.system() == "Darwin" and platform.machine() == "arm64":
            try:
                import mlx_whisper
                self._mlx, self._repo = mlx_whisper, self.MLX_REPOS.get(model, model)
                self._mlx_call(np.zeros(16_000, np.float32), None)  # warm-up: load weights + compile kernels
                self.backend = f"mlx:{model}"
            except Exception as e:
                log.warning("MLX Whisper kullanılamadı (%s) -> CPU", str(e)[:100])
        if not self.backend:
            from faster_whisper import WhisperModel
            self._m = WhisperModel("small" if model == "turbo" else model, device="cpu", compute_type="int8")
            self.backend = f"cpu:{model}"
        log.info("Whisper hazır: %s (%.1f s)", self.backend, time.perf_counter() - t0)

    def _mlx_call(self, audio: np.ndarray, hotwords: str | None):
        r = self._mlx.transcribe(audio, path_or_hf_repo=self._repo, language=self.language,
                                 temperature=0.0, condition_on_previous_text=False,
                                 initial_prompt=hotwords, verbose=None)
        return [(s.get("text", ""), s.get("no_speech_prob", 0.0), s.get("avg_logprob", 0.0))
                for s in r.get("segments", [])]

    def _cpu_call(self, audio: np.ndarray, hotwords: str | None):
        segments, _ = self._m.transcribe(
            audio, language=self.language, beam_size=1, vad_filter=False,
            condition_on_previous_text=False, without_timestamps=True, hotwords=hotwords)
        return [(s.text, s.no_speech_prob, s.avg_logprob) for s in segments]

    def __call__(self, audio: np.ndarray, hotwords: str | None = None) -> str | None:
        t0 = time.perf_counter()
        segs = (self._mlx_call if self.backend.startswith("mlx") else self._cpu_call)(audio, hotwords)
        # no_speech_prob high + low confidence = model is guessing over noise
        text = " ".join(t for t, nsp, lp in segs if not (nsp > 0.6 and lp < -0.8)).strip()
        self.last_ms = (time.perf_counter() - t0) * 1000
        n = _norm(text)
        if len(n) < 2 or any(j in n for j in _JUNK) and len(n) < 40:
            log.debug("drop: %r", text)
            return None
        log.info("duydum (%.0f ms): %s", self.last_ms, text)
        return text
