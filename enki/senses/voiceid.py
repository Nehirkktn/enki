"""Ses kimliği: konuşan Nehir mi? Resemblyzer (GE2E, 256-d d-vector), cosine benzerlik.

Ölçüm (6 Kas, TTS sesleriyle): aynı konuşmacı 0.90-0.93, farklı konuşmacı 0.52-0.67 → eşik 0.72.
Gerçek mikrofon skorları düşürür: her karar data/enki.log'a skoruyla yazılır, eşik ENKI_SES_ESIK ile ayarlanır.
Kayıt: data/ses_kaydi.npz  (E ∈ R^{k×256}, satırlar birim; ağırlık merkezi c = norm(mean(E)))
Karar: s = c·e  — kısa (<1 sn) cümlelerde embedding güvenilmez → None (bilinmiyor, Nehir sayılır).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np

log = logging.getLogger("enki.voiceid")

SR = 16_000
THRESHOLD = float(os.getenv("ENKI_SES_ESIK", "0.72"))
MIN_S = 2.0  # 6 Kas: tek kelime ("Evet.", ~1 sn) Nehir için 0.49-0.52 verdi → kısa sesler güvenilmez
ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "data" / "ses_kaydi.npz"


class ResemblyzerEncoder:
    def __init__(self):
        from resemblyzer import VoiceEncoder, preprocess_wav  # pip install resemblyzer (torch ile gelir)
        self._enc, self._pre = VoiceEncoder("cpu", verbose=False), preprocess_wav

    def __call__(self, audio: np.ndarray) -> np.ndarray | None:
        wav = self._pre(audio.astype(np.float32), source_sr=SR)  # ses gücü normalize + sessizlik kırpma
        if len(wav) < MIN_S * SR * 0.75:  # sessizlik kırpıldıktan sonra en az ~1.5 sn konuşma
            return None
        return self._enc.embed_utterance(wav)


class VoiceID:
    def __init__(self, path: Path = PATH, threshold: float = THRESHOLD, encoder=None):
        self.path, self.threshold = path, threshold
        self._encoder = encoder
        self.E = np.empty((0, 256), np.float32)
        if path.exists():
            self.E = np.load(path)["E"]
            log.info("ses kaydı: %d örnek", len(self.E))

    @property
    def encoder(self):
        if self._encoder is None:
            self._encoder = ResemblyzerEncoder()
        return self._encoder

    @property
    def enrolled(self) -> bool:
        return len(self.E) >= 3

    @property
    def enabled_for_use(self) -> bool:
        return self.enrolled

    @property
    def centroid(self) -> np.ndarray:
        c = self.E.mean(0)
        return c / (np.linalg.norm(c) or 1.0)

    def embed(self, audio: np.ndarray) -> np.ndarray | None:
        if len(audio) < MIN_S * SR:
            return None
        e = self.encoder(audio)
        return None if e is None else e / (np.linalg.norm(e) or 1.0)

    def score(self, audio: np.ndarray) -> float | None:
        if not self.enrolled:
            return None
        e = self.embed(audio)
        return None if e is None else float(self.centroid @ e)

    def is_owner(self, audio: np.ndarray) -> tuple[bool | None, float | None]:
        """(True: Nehir, False: başkası, None: karar verilemedi), skor."""
        s = self.score(audio)
        return (None if s is None else s >= self.threshold), s

    def add(self, e: np.ndarray) -> None:
        self.E = np.vstack([self.E, e[None].astype(np.float32)])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(self.path, E=self.E)


CUMLELER = [
    "Merhaba Enki, ben Nehir. Bugün biraz yorgunum ama çalışmam lazım.",
    "Lineer cebirde matris çarpımı ve determinant konularına bakacağım.",
    "Şu kahveyi bitirince kodun geri kalanını yazarım herhalde.",
    "Enki, yarın sabah dokuzda dersim var, bana hatırlatır mısın?",
    "Bu hatayı üç saattir arıyorum ve hâlâ nerede olduğunu bilmiyorum.",
    "Akşam yemeğinden sonra biraz yürüyüşe çıkmayı düşünüyorum.",
    "Bugün hava çok güzel, pencereyi açsam iyi olur.",
    "Vize haftası yaklaşıyor, planımı şimdiden yapmam gerekiyor.",
    "Sence bu fikir işe yarar mı, yoksa baştan mı düşünelim?",
    "Tamam, şimdi biraz mola verip sonra devam edeceğim.",
]


def enroll(seconds: float = 5.0, reset: bool = False) -> int:
    """Terminalde 10 cümle okutur, her birinin ses imzasını kaydeder. Mevcut kayda ekler."""
    import sounddevice as sd

    vid = VoiceID()
    if reset:
        vid.E = vid.E[:0]
    print("Ses kaydı: her cümlede Enter'a bas, sonra normal sesinle oku (5 sn).")
    print("Sessiz bir yerde ol; bir kısmını masadaki normal duruşunla, bir kısmını biraz uzaktan oku.\n")
    for i, cumle in enumerate(CUMLELER, 1):
        input(f"[{i}/{len(CUMLELER)}] Enter → oku:  “{cumle}”")
        rec = sd.rec(int(seconds * SR), samplerate=SR, channels=1, dtype="float32")
        sd.wait()
        e = vid.embed(rec[:, 0])
        if e is None:
            print("   ✗ ses çok kısa/sessiz, bu cümle atlandı")
            continue
        sim = f" (öncekilerle benzerlik {float(vid.centroid @ e):.2f})" if vid.enrolled else ""
        vid.add(e)
        print(f"   ✓ kaydedildi{sim}")
    vid.save()
    print(f"\nBitti: {len(vid.E)} örnek → {vid.path}")
    return len(vid.E)
