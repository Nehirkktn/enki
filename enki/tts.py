"""Enki'nin sesi: edge-tts Ahmet + TON tonlaması + R1 robot efekti (6 Kas; ENKI_SES_EFEKT=jarvis eskisi) + gülme kayıtları (varsayılan kapalı).

Beyin cevabı etiketli verir:  "[smug] Tabii ilk denemede değil. [laugh] Şaka şaka."
- Duygu etiketi ([excited] [smug] [blush] [stern]) arkasından gelen cümlelerin tonunu belirler.
- [laugh] kısa kıkırdama, [kahkaha] uzun gülme krizi çalar (voices/gulme/kisa, voices/gulme/uzun).
- Bilinmeyen etiketler (ör. gözler için [angry]) sesi etkilemez; hiçbir etiket ekrana basılmaz.

Ses kimliği sabit: perde duyguya göre DEĞİŞMEZ (değişince başka biri gibi duyuluyor).
Duygu yalnızca ses gücü, duraklama ve ±3 Hz cümle içi konturla taşınır.
"""
from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import platform
import random
import re
import shutil
import tempfile
import wave
from dataclasses import dataclass

import numpy as np

log = logging.getLogger("enki.tts")

SR = 24_000
VOICE = os.getenv("ENKI_SES", "tr-TR-AhmetNeural")
BASE_RATE = int(os.getenv("ENKI_SES_HIZ", "6"))      # %   (gülmenin hızı: hepsi bu hızda)
BASE_PITCH = int(os.getenv("ENKI_SES_PERDE", "10"))  # Hz  (genç, parlak)
EFFECT = os.getenv("ENKI_SES_EFEKT", "robot") or None   # robot (R1, 6 Kas) | jarvis | boş = efektsiz
GULME = os.getenv("ENKI_GULME", "0") == "1"                  # kapalı: kayıtlar çok kalın/yapay; 1 = aç
GULME_EFEKT = os.getenv("ENKI_GULME_EFEKT", "0") == "1"      # 1 = efekt gülmeye de uygulanır (eski davranış)
GULME_DIR = pathlib.Path(os.getenv("ENKI_GULME_DIR", pathlib.Path(__file__).resolve().parents[1] / "voices" / "gulme"))

# duygu: (hız farkı %, ses gücü farkı %, aynı duygudaki cümleler arası duraklama ms)
TON: dict[str, tuple[int, int, int]] = {
    "neutral": (0, 0, 40),
    "excited": (+3, +15, 0),
    "smug":    (0, -5, 60),
    "laugh":   (0, +8, 20),
    "blush":   (0, -12, 60),
    "stern":   (0, +10, 70),
}
KONTUR_HZ = 3
LAUGHS = {"laugh": "kisa", "kahkaha": "uzun", "laugh_big": "uzun", "laughbig": "uzun"}
SEGMENT_GAP_MS = 120

_TAG_RE = re.compile(r"\[([A-Za-z_]+)\]")
_SENT_RE = re.compile(r"[^.!?…]+[.!?…]*")


# ---------------------------------------------------------------- etiket ayrıştırma (saf, test edilebilir)
@dataclass(frozen=True, slots=True)
class Segment:
    kind: str            # "speech" | "laugh"
    emotion: str = "neutral"
    text: str = ""
    laugh: str = ""      # "kisa" | "uzun"


def segments(text: str) -> list[Segment]:
    out: list[Segment] = []
    emotion = "neutral"
    parts = _TAG_RE.split(text)  # [metin, etiket, metin, etiket, ...]
    for i, part in enumerate(parts):
        if i % 2:  # etiket
            tag = part.lower()
            if tag in LAUGHS:
                out.append(Segment("laugh", laugh=LAUGHS[tag]))
                emotion = "laugh"
            elif tag in TON:
                emotion = tag
            continue
        part = " ".join(part.split())
        if part:
            out.append(Segment("speech", emotion, part))
    return out


def strip_tags(text: str) -> str:
    return " ".join(_TAG_RE.sub(" ", text).split())


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_RE.findall(text) if s.strip()] or [text]


def contour(sentence: str, i: int, n: int) -> tuple[int, int]:
    """(hız farkı, perde farkı): düz cümlede ses baştan sona hafif iner, soru sonunda kalkar, ünlemde hızlanır."""
    dp = round(KONTUR_HZ - 2 * KONTUR_HZ * i / (n - 1)) if n > 1 else 0
    dr = 0
    if sentence.endswith("?"):
        dp += KONTUR_HZ + 2
    if sentence.endswith("!"):
        dr += 5
    return dr, dp


class LaughPicker:
    """Klasördeki kayıtlardan rastgele seçer; aynı kaydı üst üste çalmaz (birden fazla varsa)."""

    def __init__(self, root: pathlib.Path = GULME_DIR, rng: random.Random | None = None):
        self.root, self.rng, self.last = root, rng or random.Random(), {}

    def pick(self, kind: str) -> pathlib.Path | None:
        files = sorted((self.root / kind).glob("*.wav"))
        if not files:
            return None
        # 3+ kayıtta aynısını üst üste çalma; 2 kayıtta bu kural sırayla çalmaya dönüşür, o yüzden tamamen rastgele
        choices = [f for f in files if f != self.last.get(kind)] if len(files) > 2 else files
        self.last[kind] = f = self.rng.choice(choices)
        return f


# ---------------------------------------------------------------- DSP (int16 PCM, 24 kHz mono)
def trim(pcm: np.ndarray, threshold: int = 400, pad_ms: int = 12) -> np.ndarray:
    loud = np.flatnonzero(np.abs(pcm) > threshold)
    if not len(loud):
        return pcm
    pad = int(SR * pad_ms / 1000)
    return pcm[max(0, loud[0] - pad): loud[-1] + pad]


def fade(x: np.ndarray, ms: int = 10) -> np.ndarray:
    n = int(SR * ms / 1000)
    x = x.astype(np.float32)
    if len(x) > 2 * n:
        ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
        x[:n] *= ramp
        x[-n:] *= ramp[::-1]
    return x


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x.astype(np.float32))))) if len(x) else 0.0


def _one_pole(x: np.ndarray, a: float) -> np.ndarray:
    y = np.empty_like(x)
    acc, b = 0.0, 1.0 - a
    for i, v in enumerate(x):
        acc = b * v + a * acc
        y[i] = acc
    return y


def _chorus(x, base_ms, depth_ms, lfo_hz, mix):
    n = np.arange(len(x), dtype=np.float32)
    d = (base_ms + depth_ms * np.sin(2 * np.pi * lfo_hz * n / SR)) * SR / 1000
    return (1 - mix) * x + mix * np.interp(n - d, n, x, left=0.0)


def _reflections(x, taps_ms, decay):
    y = x.copy()
    for k, ms in enumerate(taps_ms, 1):
        d = int(SR * ms / 1000)
        if d < len(x):
            y[d:] += (decay ** k) * x[:-d]
    return y


def _jarvis_f(x: np.ndarray) -> np.ndarray:
    """float [-1,1] → float, normalize etmeden (parça parça uygulanabilsin diye)."""
    x = x + 0.35 * (x - _one_pole(x, 0.92))
    x = _chorus(x, 12, 1.5, 0.6, 0.22)
    return _reflections(x, (23, 41, 67, 97), 0.38)


def _normalize(x: np.ndarray) -> np.ndarray:
    x = x / max(1.0, float(np.abs(x).max()) / 0.95)
    return (x * 32767).astype(np.int16)


def jarvis(pcm: np.ndarray) -> np.ndarray:
    """'Duvarlardaki yapay zekâ' parlaklığı: presence + hafif chorus + küçük oda (robot_vision_v2 ile aynı)."""
    return _normalize(_jarvis_f(pcm.astype(np.float32) / 32768.0))


def _bp(x: np.ndarray, lo: float, hi: float, order: int) -> np.ndarray:
    from scipy.signal import butter, sosfilt
    return sosfilt(butter(order, [lo, hi], "bandpass", fs=SR, output="sos"), x)


def _comb(x: np.ndarray, ms: float, fb: float, mix: float) -> np.ndarray:
    d = int(round(SR * ms / 1000))
    y = np.copy(x)
    for i in range(d, len(y), d):  # y[n] = x[n] + fb·y[n-d], d örneklik bloklarla
        j = min(i + d, len(y))
        y[i:j] += fb * y[i - d:j - d]
    return (1 - mix) * x + mix * (1 - fb) * y


def _robot_f(x: np.ndarray) -> np.ndarray:
    """R1 'robot male' rengi (Nehir'in referans kaydından): perde DEĞİŞMEZ.
    300 Hz altı kesik + 0.85-1.9 kHz varlık bölgesi + sabit 4.1 ms tarak (metalik ~245 Hz) + doygunluk + kısa yankı."""
    peak = float(np.abs(x).max()) or 1.0   # parçanın seviyesi korunur (TON ses gücü farkları kaybolmasın)
    pk = lambda v: v / (float(np.abs(v).max()) or 1.0) * 0.9
    x = _bp(pk(x), 380, 5500, 6)
    x = x + 2.2 * _bp(x, 850, 1900, 2)
    x = _comb(x, 4.1, 0.55, 0.7)
    x = np.tanh(1.8 * pk(x)) / np.tanh(1.8)
    y = np.copy(x)
    for ms, g in ((55, .22), (110, .1), (180, .05)):
        d = int(SR * ms / 1000)
        y[d:] += g * x[:-d]
    return y / (float(np.abs(y).max()) or 1.0) * peak


def robot(pcm: np.ndarray) -> np.ndarray:
    return _normalize(_robot_f(pcm.astype(np.float32) / 32768.0))


EFFECTS = {"jarvis": jarvis, "robot": robot}
EFFECTS_F = {"jarvis": _jarvis_f, "robot": _robot_f}


def apply_effect(pcm: np.ndarray, effect: str | None = EFFECT) -> np.ndarray:
    return EFFECTS[effect](pcm) if effect in EFFECTS else pcm.astype(np.int16)


def read_wav(path: pathlib.Path) -> np.ndarray:
    with wave.open(str(path), "rb") as r:
        pcm = np.frombuffer(r.readframes(r.getnframes()), np.int16)
        sr, ch = r.getframerate(), r.getnchannels()
    if ch == 2:
        pcm = pcm.reshape(-1, 2).mean(axis=1)
    if sr != SR:
        pcm = np.interp(np.linspace(0, len(pcm) - 1, int(len(pcm) * SR / sr)), np.arange(len(pcm)), pcm)
    return pcm.astype(np.int16)


def write_wav(path: pathlib.Path, pcm: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.clip(pcm, -32767, 32767).astype(np.int16).tobytes())


# ---------------------------------------------------------------- render
async def _run(*cmd: str) -> None:
    p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, err = await p.communicate()
    if p.returncode:
        raise RuntimeError(f"{cmd[0]}: {err.decode(errors='ignore').strip()}")


async def edge_pcm(text: str, rate: int, pitch: int, volume: int) -> np.ndarray:
    """Tek cümle -> int16 PCM. edge-tts mp3 verir; macOS'ta afconvert, diğerinde ffmpeg ile çözülür."""
    import edge_tts  # pip install edge-tts

    data = bytearray()
    c = edge_tts.Communicate(text, VOICE, rate=f"{rate:+d}%", pitch=f"{pitch:+d}Hz", volume=f"{volume:+d}%")
    async for chunk in c.stream():
        if chunk["type"] == "audio":
            data += chunk["data"]
    if not data:
        raise RuntimeError("edge-tts ses döndürmedi")
    with tempfile.TemporaryDirectory() as td:
        mp3, wav = pathlib.Path(td) / "a.mp3", pathlib.Path(td) / "a.wav"
        mp3.write_bytes(data)
        if platform.system() == "Darwin":
            await _run("afconvert", str(mp3), str(wav), "-f", "WAVE", "-d", f"LEI16@{SR}", "-c", "1")
        elif shutil.which("ffmpeg"):
            await _run("ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3), "-ac", "1", "-ar", str(SR), str(wav))
        else:
            raise RuntimeError("mp3 çözmek için afconvert/ffmpeg gerekli")
        return read_wav(wav)


class EnkiVoice:
    """Cevabı parçalara ayırır, konuşma parçalarını paralel üretir, gülmeleri araya koyar, tek dosya çalar."""

    def __init__(self, synth=edge_pcm, laughs: LaughPicker | None = None, effect: str | None = EFFECT,
                 laugh_on: bool = GULME, laugh_effect: bool = GULME_EFEKT):
        self.synth, self.laughs, self.effect = synth, laughs or LaughPicker(), effect
        self.laugh_on, self.laugh_effect = laugh_on, laugh_effect

    async def _speech(self, seg: Segment) -> np.ndarray:
        dr0, dv, gap = TON.get(seg.emotion, TON["neutral"])
        parts = sentences(seg.text)
        rendered = await asyncio.gather(*(
            self.synth(s, BASE_RATE + dr0 + dr, BASE_PITCH + dp, dv)
            for i, s in enumerate(parts) for dr, dp in [contour(s, i, len(parts))]
        ))
        silence = np.zeros(int(SR * gap / 1000), np.float32)
        out: list[np.ndarray] = []
        for k, pcm in enumerate(rendered):
            out.append(fade(trim(pcm)))
            if k < len(rendered) - 1:
                out.append(silence)
        return np.concatenate(out)

    def _laugh(self, kind: str, target_rms: float) -> np.ndarray | None:
        f = self.laughs.pick(kind)
        if f is None:
            log.warning("gülme kaydı yok: %s/%s", self.laughs.root, kind)
            return None
        x = trim(read_wav(f), threshold=300, pad_ms=30).astype(np.float32)
        peak = float(np.abs(x).max()) or 1.0
        # aralarında sessizlik olan kıkırdamalarda RMS düşük çıkar; sadece RMS'e göre büyütmek patlatır
        gain = min(target_rms / (rms(x) or 1.0) * 0.9, 0.8 * 32767 / peak)
        return fade(x * gain)

    async def render(self, text: str) -> np.ndarray | None:
        segs = segments(text)
        if not segs:
            return None
        speech = await asyncio.gather(*(self._speech(s) for s in segs if s.kind == "speech"))
        it = iter(speech)
        audio: list[np.ndarray | None] = [next(it) if s.kind == "speech" else None for s in segs]
        levels = [rms(a) for a in audio if a is not None]
        level = float(np.median(levels)) if levels else 3000.0
        for i, s in enumerate(segs):
            if s.kind == "laugh" and self.laugh_on:
                audio[i] = self._laugh(s.laugh, level)
        fx = EFFECTS_F.get(self.effect) if self.effect else None
        pieces = []
        for s, a in zip(segs, audio):
            if a is None or not len(a):
                continue
            x = np.asarray(a, np.float32) / 32768.0
            # efekt sadece konuşmaya: robotik efekt gülmeyi metalik yapıyordu
            if fx and (s.kind == "speech" or self.laugh_effect):
                x = fx(x)
            pieces.append(x)
        if not pieces:
            return None
        gap = np.zeros(int(SR * SEGMENT_GAP_MS / 1000), np.float32)
        joined = np.concatenate([p for a in pieces for p in (a, gap)][:-1])
        return _normalize(joined)

    async def speak(self, text: str) -> None:
        pcm = await self.render(text)
        if pcm is not None:
            await play(pcm)


async def play(pcm: np.ndarray) -> None:
    player = shutil.which("afplay") or shutil.which("aplay")  # macOS | Linux/Pi
    if not player:
        raise RuntimeError("ses çalıcı (afplay/aplay) yok")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = pathlib.Path(f.name)
    try:
        write_wav(path, pcm)
        p = await asyncio.create_subprocess_exec(player, str(path))
        await p.wait()
    finally:
        path.unlink(missing_ok=True)


class VoiceSpeaker:
    """Beynin Speaker portu: etiketsiz metni yazdırır, etiketli metni seslendirir."""

    def __init__(self, voice: EnkiVoice, printer):
        self.voice, self.printer = voice, printer

    async def say(self, text: str) -> None:
        await self.printer.say(strip_tags(text))
        try:
            await self.voice.speak(text)
        except Exception as e:
            log.warning("ses üretilemedi, sadece yazı: %s", e)
