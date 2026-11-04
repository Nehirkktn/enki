"""Enki ses seçimi: aynı cümleleri her adayda çalar, dosyaları voices/aday/ altına kaydeder.

  ~/.venv/bin/python tools/ses_dinle.py            # tüm hazır adaylar
  ~/.venv/bin/python tools/ses_dinle.py 2 4        # sadece 2. ve 4. aday
  ~/.venv/bin/python tools/ses_dinle.py ahmet      # Ahmet'i şekillendirme ızgarası (A–H)
  ~/.venv/bin/python tools/ses_dinle.py ahmet C F  # ızgaradan sadece C ve F
  ~/.venv/bin/python tools/ses_dinle.py h          # 2. tur: H etrafında ince ayar (H0–H7)
  ~/.venv/bin/python tools/ses_dinle.py h H2 H5    # 2. turdan sadece seçtiklerin
  ~/.venv/bin/python tools/ses_dinle.py x          # 3. tur: genç + enerjik + parlak (X1–X6)
  ~/.venv/bin/python tools/ses_dinle.py duygu X6   # aynı konuşma: düz vs duyguya göre tonlanmış

robot_vision_v2'nin ses motorunu (edge-tts + efektler) kullanır; ~/.venv içinde zaten kurulu.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(pathlib.Path.home() / "Downloads" / "robot_vision_v2"))
from robot import tts as T  # noqa: E402

LINES = [
    "Pazar günü saat üç buçukta masa başındasın. Ya çok çalışkansın ya da bir şeyden kaçıyorsun.",
    "Beni sen yazdın Nehir. Yani if-else'sem bile, o else'leri senin kodun dolduruyor.",
    "Lineer cebir PDF'i on iki gündür açılmadı. Ben sadece söylüyorum.",
]

# Izgara için ek efektler: aynı DSP, farklı doz
T.EFFECTS["robot_hafif"] = lambda pcm, sr: T.robotize(pcm, sr, mix=0.18, feedback=0.18)
T.EFFECTS["jarvis_robot"] = lambda pcm, sr: T.jarvisize(T.robotize(pcm, sr, mix=0.15, feedback=0.15), sr)


def parlak(pcm, sr):
    """JARVIS'ten daha parlak, daha az oda: güçlü presence + hafif chorus, kısa yansıma.
    Enerjik konuşmada uzun yankı sesi bulanıklaştırır, o yüzden oda küçük tutuldu."""
    import numpy as np
    x = pcm.astype(np.float32) / 32768.0
    x = x + 0.6 * (x - T._one_pole(x, 0.92))          # presence/air lift
    x = T._chorus(x, sr, base_ms=10, depth_ms=1.0, lfo_hz=0.8, mix=0.15)
    x = T._early_reflections(x, sr, (17, 29), decay=0.25)
    x /= max(1.0, float(np.abs(x).max()) / 0.95)
    return (x * 32767).astype(np.int16)


T.EFFECTS["parlak"] = parlak

AHMET = "tr-TR-AhmetNeural"

# (etiket, edge sesi, hız, perde, efekt)
ADAYLAR = [
    ("1  Ahmet, düz",                    AHMET, "-8%",  "-6Hz", None),
    ("2  Ahmet + JARVIS efekti",         AHMET, "-8%",  "-6Hz", "jarvis"),
    ("3  Ahmet + robot efekti",          AHMET, "-8%",  "-6Hz", "robot"),
    ("4  Ahmet, yavaş + Baymax efekti",  AHMET, "-18%", "+2Hz", "baymax"),
    ("5  Emel (kadın), düz",             "tr-TR-EmelNeural", "+0%", "+0Hz", None),
    ("6  Piper DFKI (internetsiz)",      None, None, None, None),
]

# 1. tur: Ahmet'i hız/perde/efekt eksenlerinde birer adım oynat
IZGARA = {
    "A": ("derin + yavaş, efektsiz",          "-12%", "-15Hz", None),
    "B": ("derin + yavaş + JARVIS",           "-12%", "-15Hz", "jarvis"),
    "C": ("derin + normal hız + JARVIS",      "-4%",  "-12Hz", "jarvis"),
    "D": ("derin + hafif robot",              "-8%",  "-12Hz", "robot_hafif"),
    "E": ("JARVIS + hafif robot karışımı",    "-8%",  "-10Hz", "jarvis_robot"),
    "F": ("hızlı + esprili, efektsiz",        "+6%",  "-4Hz",  None),
    "G": ("hızlı + esprili + hafif robot",    "+6%",  "-4Hz",  "robot_hafif"),
    "H": ("genç / Peter Parker",              "+8%",  "+6Hz",  None),
}

# 2. tur: H etrafında ince ayar (H0 = referans)
IZGARA_H = {
    "H0": ("H, referans",                       "+8%",  "+6Hz",  None),
    "H1": ("H, biraz daha tok (perde düşük)",   "+8%",  "+3Hz",  None),
    "H2": ("H, daha genç (perde yüksek)",       "+8%",  "+10Hz", None),
    "H3": ("H, daha sakin (yavaş)",             "+3%",  "+6Hz",  None),
    "H4": ("H, daha enerjik (hızlı)",           "+13%", "+6Hz",  None),
    "H5": ("H + hafif robot dokunuşu",          "+8%",  "+6Hz",  "robot_hafif"),
    "H6": ("H + JARVIS parlaklığı",             "+8%",  "+6Hz",  "jarvis"),
    "H7": ("H + hafif robot + JARVIS",          "+8%",  "+6Hz",  "jarvis_robot"),
}


# 3. tur: genç + enerjik + parlak (H2 perdesi, H4 hızı, H6 parlaklığı)
IZGARA_X = {
    "X1": ("genç+enerjik+JARVIS (doğrudan birleşim)", "+13%", "+10Hz", "jarvis"),
    "X2": ("X1'in bir tık yumuşağı",                  "+11%", "+8Hz",  "jarvis"),
    "X3": ("X1'in bir tık fazlası",                   "+15%", "+12Hz", "jarvis"),
    "X4": ("X1 efektsiz (efektin katkısını duy)",     "+13%", "+10Hz", None),
    "X5": ("X1 + hafif robot",                        "+13%", "+10Hz", "jarvis_robot"),
    "X6": ("genç+enerjik+PARLAK (daha az yankı)",     "+13%", "+10Hz", "parlak"),
}


# Duygu → ses: her cümle, beynin ürettiği duygu etiketine göre tabana göre biraz kaydırılır.
# (hız farkı %, perde farkı Hz). Gözlerdeki [smug]/[blush]/[angry] etiketlerinin aynısı.
DUYGU = {
    "neutral": (0, 0),
    "excited": (+8, +8),     # heyecan: hızlı + tiz
    "smug":    (-6, -6),     # alaycı: yavaş + tok, kelimeleri tadını çıkararak
    "laugh":   (+5, +4),
    "blush":   (-2, +12),    # iltifat aldı: tiz, biraz yavaş
    "stern":   (-12, -12),   # sert mod: yavaş, alçak, esprisiz
}

SAHNE = [  # küçük bir konuşma: aynı ses, cümle cümle farklı duygu
    ("excited", "Nehir! Testlerin hepsi geçti, on altıda on altı!"),
    ("smug",    "Tabii ilk denemede değil. Üçüncü denemede. Ama saymıyorum."),
    ("blush",   "Ne? Beni mi övdün az önce? Kaydettim bunu, geri alamazsın."),
    ("laugh",   "Şaka şaka. Ama hafızama yazdım, gerçekten."),
    ("stern",   "Şimdi ciddi bir şey söyleyeceğim. Lineer cebir vizesi cuma."),
]


def _shift(base: str, delta: int, unit: str) -> str:
    v = int(base.rstrip(unit)) + delta
    return f"{v:+d}{unit}"


def duygu(base_key: str) -> None:
    _, hiz, perde, efekt = IZGARA_X.get(base_key, IZGARA_X["X1"])
    out = ROOT / "voices" / "aday" / "duygu" / base_key
    out.mkdir(parents=True, exist_ok=True)
    for mod in ("duz", "duygulu"):
        print(f"\n▶ {'DÜZ (her cümle aynı ton)' if mod == 'duz' else 'DUYGULU (cümle cümle ton değişiyor)'}   taban: {base_key}")
        for i, (tag, line) in enumerate(SAHNE, 1):
            dr, dp = DUYGU[tag] if mod == "duygulu" else (0, 0)
            wav = out / f"{mod}_{i}.wav"
            if not render(wav, AHMET, _shift(hiz, dr, "%"), _shift(perde, dp, "Hz"), efekt, line):
                return
            print(f"   [{tag if mod == 'duygulu' else '-':7}] {line}")
            subprocess.run(["afplay", str(wav)])
    print(f"\nDosyalar: {out}")


def render(path: pathlib.Path, voice: str, rate: str, pitch: str, effect: str | None, line: str) -> bool:
    if path.exists():
        return True
    try:
        T.EdgeEngine(voice, rate=rate, pitch=pitch).render(line, path)
        T.apply_effect(path, effect)
        return True
    except Exception as e:
        print(f"   üretilemedi: {e}")
        return False


def izgara(secim: set[str], grid: dict, lines: list[int]) -> None:
    out = ROOT / "voices" / "aday" / "ahmet"
    out.mkdir(parents=True, exist_ok=True)
    for kod, (etiket, hiz, perde, efekt) in grid.items():
        if secim and kod not in secim:
            continue
        print(f"\n▶ {kod}  {etiket}   (hız {hiz}, perde {perde}, efekt {efekt or '-'})")
        for n in lines:
            wav = out / f"{kod}_{n + 1}.wav"
            if not render(wav, AHMET, hiz, perde, efekt, LINES[n]):
                break
            print(f"   {LINES[n][:60]}...")
            subprocess.run(["afplay", str(wav)])
    print(f"\nDosyalar: {out}  (tekrar dinlemek için Finder'da boşluk tuşu)")


def adaylar(secili: set[int]) -> None:
    out = ROOT / "voices" / "aday"
    out.mkdir(parents=True, exist_ok=True)
    for i, (etiket, ses, hiz, perde, efekt) in enumerate(ADAYLAR, 1):
        if secili and i not in secili:
            continue
        print(f"\n▶ {etiket}")
        for n, line in enumerate(LINES, 1):
            if ses is None:  # Piper: önceden üretilmiş örnekler
                wav = ROOT / "voices" / f"piper_{n}.wav"
                if not wav.exists():
                    print("   (piper örneği yok, atlandı)"); break
            else:
                wav = out / f"{i}_{n}.wav"
                if not render(wav, ses, hiz, perde, efekt, line):
                    break
            print(f"   {n}. {line[:60]}...")
            subprocess.run(["afplay", str(wav)])
    print(f"\nDosyalar: {out}  (tekrar dinlemek için Finder'da boşluk tuşu)")


def main() -> None:
    args = sys.argv[1:]
    mode = args[0].lower() if args else ""
    if mode == "ahmet":
        izgara({a.upper() for a in args[1:]}, IZGARA, [1])
    elif mode == "h":
        # iki cümle: biri esprili-sakin, biri İngilizce kelimeli
        izgara({a.upper() for a in args[1:]}, IZGARA_H, [1, 2])
    elif mode == "duygu":
        duygu(args[1].upper() if len(args) > 1 else "X1")
    elif mode == "x":
        izgara({a.upper() for a in args[1:]}, IZGARA_X, [1, 2])
    else:
        adaylar({int(a) for a in args})


if __name__ == "__main__":
    main()
