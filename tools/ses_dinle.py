"""Enki ses seçimi: aynı 3 cümleyi her adayda çalar, dosyaları voices/aday/ altına kaydeder.

  ~/.venv/bin/python tools/ses_dinle.py            # hepsini sırayla dinle
  ~/.venv/bin/python tools/ses_dinle.py 2 4        # sadece 2. ve 4. adayı dinle

robot_vision_v2'nin ses motorunu (edge-tts + efektler) kullanır; ~/.venv içinde zaten kurulu.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(pathlib.Path.home() / "Downloads" / "robot_vision_v2"))
from robot.tts import EdgeEngine, apply_effect  # noqa: E402

LINES = [
    "Pazar günü saat üç buçukta masa başındasın. Ya çok çalışkansın ya da bir şeyden kaçıyorsun.",
    "Beni sen yazdın Nehir. Yani if-else'sem bile, o else'leri senin kodun dolduruyor.",
    "Lineer cebir PDF'i on iki gündür açılmadı. Ben sadece söylüyorum.",
]

# (etiket, edge sesi, hız, perde, efekt)
ADAYLAR = [
    ("1  Ahmet, düz",                    "tr-TR-AhmetNeural", "-8%",  "-6Hz", None),
    ("2  Ahmet + JARVIS efekti",         "tr-TR-AhmetNeural", "-8%",  "-6Hz", "jarvis"),
    ("3  Ahmet + robot efekti",          "tr-TR-AhmetNeural", "-8%",  "-6Hz", "robot"),
    ("4  Ahmet, yavaş + Baymax efekti",  "tr-TR-AhmetNeural", "-18%", "+2Hz", "baymax"),
    ("5  Emel (kadın), düz",             "tr-TR-EmelNeural",  "+0%",  "+0Hz", None),
    ("6  Piper DFKI (internetsiz)",      None,                None,   None,   None),
    # Microsoft'un çok dilli sesleri: aynı ücretsiz motor, Türkçeyi hafif yabancı aksanla okur
    ("7  Andrew (çok dilli, erkek)",     "en-US-AndrewMultilingualNeural", "-5%", "+0Hz", None),
    ("8  Brian (çok dilli, erkek)",      "en-US-BrianMultilingualNeural",  "-5%", "+0Hz", None),
    ("9  Andrew + JARVIS efekti",        "en-US-AndrewMultilingualNeural", "-5%", "+0Hz", "jarvis"),
    # Diğer internetsiz Türkçe Piper sesleri (ilk seferde ~60 MB indirir, piper-tts gerekir)
    ("10 Piper Fahrettin (internetsiz)", "piper:tr_TR-fahrettin-medium", None, None, None),
    ("11 Piper Fettah (internetsiz)",    "piper:tr_TR-fettah-medium",    None, None, None),
]


def main() -> None:
    secili = {int(a) for a in sys.argv[1:]} or set(range(1, len(ADAYLAR) + 1))
    out = ROOT / "voices" / "aday"
    out.mkdir(parents=True, exist_ok=True)
    for i, (etiket, ses, hiz, perde, efekt) in enumerate(ADAYLAR, 1):
        if i not in secili:
            continue
        print(f"\n▶ {etiket}")
        for n, line in enumerate(LINES, 1):
            if ses is None:  # Piper: önceden üretilmiş örnekler
                wav = ROOT / "voices" / f"piper_{n}.wav"
                if not wav.exists():
                    print("   (piper örneği yok, atlandı)"); break
            else:
                wav = out / f"{i}_{n}.wav"
                if not wav.exists():
                    try:
                        if ses.startswith("piper:"):
                            from robot.tts import PiperEngine
                            PiperEngine(ses[6:], models_dir=ROOT / "models" / "piper").render(line, wav)
                        else:
                            EdgeEngine(ses, rate=hiz, pitch=perde).render(line, wav)
                        apply_effect(wav, efekt)
                    except ImportError:
                        print("   piper-tts kurulu değil: ~/.venv/bin/pip install piper-tts"); break
                    except Exception as e:
                        print(f"   üretilemedi: {e}"); break
            print(f"   {n}. {line[:60]}...")
            subprocess.run(["afplay", str(wav)])
    print(f"\nDosyalar: {out}  (tekrar dinlemek için Finder'da boşluk tuşu)")


if __name__ == "__main__":
    main()
