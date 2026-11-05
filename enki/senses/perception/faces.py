"""Face detection (YuNet) + identity (SFace 128-d embeddings, cosine similarity)."""
from __future__ import annotations

import logging
import urllib.request
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("faces")

_ZOO = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models"
YUNET = ("face_detection_yunet_2023mar.onnx", f"{_ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx")
SFACE = ("face_recognition_sface_2021dec.onnx", f"{_ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx")

# SFace paper/zoo operating point for cosine similarity. Tune on YOUR data later (ROC curve).
COSINE_THRESHOLD = 0.363


def ensure_model(models_dir: Path, spec: tuple[str, str]) -> Path:
    name, url = spec
    path = models_dir / name
    if not path.exists() or path.stat().st_size < 10_000:  # <10KB = an error page, not a model
        models_dir.mkdir(parents=True, exist_ok=True)
        log.info("downloading %s ...", name)
        urllib.request.urlretrieve(url, path)
    return path


class FaceDetector:
    """Detects on a downscaled frame, returns rows in ORIGINAL coordinates.

    Row layout (YuNet): [x, y, w, h, 5×(lx, ly) landmarks, score] -> shape (N, 15)
    """

    def __init__(self, model: Path, work_width: int = 320, score: float = 0.8, nms: float = 0.3) -> None:
        self.work_width = work_width
        self._net = cv2.FaceDetectorYN.create(str(model), "", (work_width, work_width), score, nms, 50)
        self._in_size: tuple[int, int] | None = None

    def detect(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        s = self.work_width / w
        size = (self.work_width, round(h * s))
        if size != self._in_size:
            self._net.setInputSize(size)
            self._in_size = size
        _, faces = self._net.detect(cv2.resize(frame, size, interpolation=cv2.INTER_AREA))
        if faces is None:
            return np.empty((0, 15), np.float32)
        faces[:, :14] /= s  # one vectorised rescale of boxes + landmarks
        return faces


class FaceEmbedder:
    def __init__(self, model: Path) -> None:
        self._net = cv2.FaceRecognizerSF.create(str(model), "")

    def embed(self, frame: np.ndarray, face_row: np.ndarray) -> np.ndarray:
        aligned = self._net.alignCrop(frame, face_row)  # similarity transform on 5 landmarks
        v = self._net.feature(aligned).ravel().astype(np.float32)
        return v / np.linalg.norm(v)  # unit sphere -> dot product == cosine similarity


class OwnerGallery:
    """G ∈ R^{k×128}, rows unit-norm. similarity(e) = max(G·e): one matmul, k templates.

    Max-over-templates beats a single mean template: your face at 30° yaw and
    frontal live in different regions of embedding space; the mean lands in neither.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.G = np.empty((0, 128), np.float32)
        if path.exists():
            self.G = np.load(path)["G"]
            log.info("loaded %d owner templates", len(self.G))

    def __len__(self) -> int:
        return len(self.G)

    def similarity(self, e: np.ndarray) -> float:
        return float((self.G @ e).max()) if len(self.G) else -1.0

    def try_add(self, e: np.ndarray, max_redundancy: float = 0.92) -> bool:
        """Reject near-duplicates so templates cover pose/light variance instead of 20 copies of one frame."""
        if len(self.G) and self.similarity(e) > max_redundancy:
            return False
        self.G = np.vstack([self.G, e[None]])
        return True

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(self.path, G=self.G)


def largest(faces: np.ndarray) -> np.ndarray | None:
    return faces[np.argmax(faces[:, 2] * faces[:, 3])] if len(faces) else None
