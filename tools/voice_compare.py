"""Blind-ish A/B test of every configured TTS backend on the same Enki lines.

  python tools/voice_compare.py              # synth + play all, save to voices/
  python tools/voice_compare.py --no-play    # just save files
  python tools/voice_compare.py "kendi cümlen"

Each backend is timed: latency matters as much as quality for a robot that talks back.
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from enki.tts import available_backends, play  # noqa: E402

LINES = [
    "Pazar günü saat üç buçukta masa başındasın. Ya çok çalışkansın ya da bir şeyden kaçıyorsun.",
    "Beni sen yazdın Nehir. Yani if-else'sem bile, o else'leri senin kodun dolduruyor.",
    "Lineer cebir PDF'i on iki gündür açılmadı. Ben sadece söylüyorum.",
]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", help="tek bir cümle test et")
    ap.add_argument("--no-play", action="store_true")
    args = ap.parse_args()

    backends = available_backends()
    if not backends:
        print("Hiç TTS backend'i yok. README'deki kurulum adımlarına bak.")
        return
    print("Bulunan sesler:", ", ".join(backends))

    out = pathlib.Path("voices")
    out.mkdir(exist_ok=True)
    lines = [args.text] if args.text else LINES

    results: list[tuple[str, float]] = []
    for name, tts in backends.items():
        print(f"\n━━━ {name} ━━━")
        total = 0.0
        for i, line in enumerate(lines, 1):
            t0 = time.perf_counter()
            try:
                audio = await tts.synth(line)
            except Exception as e:
                print(f"  ✗ {e}")
                break
            dt = time.perf_counter() - t0
            total += dt
            path = out / f"{name}_{i}.{audio.ext}"
            path.write_bytes(audio.data)
            print(f"  {i}. {dt*1000:5.0f} ms  → {path}")
            if not args.no_play:
                await play(audio)
        else:
            results.append((name, total / len(lines)))

    if results:
        print("\nOrtalama gecikme (düşük = Enki daha hızlı cevap verir):")
        for name, avg in sorted(results, key=lambda x: x[1]):
            print(f"  {name:8s} {avg*1000:6.0f} ms")


if __name__ == "__main__":
    asyncio.run(main())
