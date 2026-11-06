"""Enki: tek beyin, çok gövde. Terminal, Mac'in kamerası + mikrofonu, telefon ve yüz ekranı aynı beyne bağlanır.

  enki                      # her şey açık: ses, kamera, mikrofon, gövde merkezi (telefon linki basılır)
  enki --sessiz             # Enki sesli cevap vermez (ya da ENKI_SESSIZ=1)
  enki --no-cam --no-mic    # sadece klavye
  enki --no-hub             # telefon/yüz bağlantısı yok
  python -m enki --kayit    # yüzünü öğret (kamera, ~30 sn)
  python -m enki --ses-kayit  # sesini öğret (10 cümle okursun)
  /kendi <metin>            # kendi kendine konuşma simülasyonu (Overheard)
  /yuz                      # kamera seni gördü (FaceSeen)
  /gor                      # kamera şu an ne görüyor
  Enki konuşurken "Enki dur" de (ya da yaz): susar
  Ayrıntılı kayıt: data/enki.log (göz, kulak, gövde ne yaptı)
  /hafiza                   # Enki ne biliyor
  /cik
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import threading
import time
from pathlib import Path

from .brain import Brain, FaceLeft, FaceSeen, Overheard, Shutdown, UserSaid
from .config import Config
from .govde import BodySpeaker, fsm_face
from .llm import build_llm
from .memory import Memory
from .tts import strip_tags

DIM, CYAN, YELLOW, RESET = "\033[2m", "\033[36m", "\033[33m", "\033[0m"


class ConsoleSpeaker:
    """Prefixes each reply with the brain that produced it; fallback is yellow so it can't hide."""

    def __init__(self, brain_name=lambda: ""):
        self.brain_name = brain_name

    async def say(self, text: str) -> None:
        name = self.brain_name()
        tag = f" {DIM if name == 'claude' else YELLOW}[{name}]{RESET}{CYAN}" if name else ""
        print(f"\r{CYAN}Enki{tag} ›{RESET} {strip_tags(text)}\n{DIM}sen ›{RESET} ", end="", flush=True)


TYPED = [-1e9]  # son klavye girişi (time.monotonic): kulak buna göre yazma moduna geçer
VISION = [None]


def gor_raporu() -> str:
    v = VISION[0]
    if v is None:
        return "  göz kapalı (--no-cam)"
    if not v.ok:
        return "  kamera açılamadı: data/enki.log'a bak (çoğunlukla Terminal'in Kamera izni)"
    p = v.percept
    if p is None:
        return "  kamera açık, henüz kare yok"
    sim = f"{p.sim_ema:.2f}" if p.sim_ema is not None else "-"
    return f"  durum: {p.state.name} · yüz sayısı: {len(p.faces)} · benzerlik: {sim} (eşik 0.36) · bakıyor: {p.facing}"


async def stdin_loop(brain: Brain, mem: Memory, interrupt=lambda: None) -> None:
    loop = asyncio.get_running_loop()
    print(f"{DIM}sen ›{RESET} ", end="", flush=True)
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            brain.post(Shutdown())
            return
        line = line.strip()
        TYPED[0] = time.monotonic()
        if not line:
            continue
        from .senses.ear import stop_command
        if stop_command(line):  # yazarak da sadece "enki dur" keser
            interrupt()
            continue
        if line == "/cik":
            brain.post(Shutdown())
            return
        if line == "/hafiza":
            print("\n".join(f"  • {f}" for f in mem.facts()) or "  (boş)")
        elif line == "/gor":
            print(gor_raporu())
        elif line == "/yuz":
            brain.post(FaceSeen())
        elif line.startswith("/kendi "):
            brain.post(Overheard(line[7:]))
        else:
            brain.post(UserSaid(line))


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")          # "setPreferableTarget" uyarıları
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")  # Whisper "Fetching 4 files" çubuğu
os.environ.setdefault("TQDM_DISABLE", "1")


def build_voice():
    """EnkiVoice ya da None. --sessiz olsa da telefona ses gidebilsin diye konuşmacıdan ayrı kurulur."""
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        print(f"{YELLOW}ses kapalı: edge-tts kurulu değil (pip install edge-tts){RESET}")
        return None
    from .tts import EnkiVoice

    return EnkiVoice()


def launch_desktop_face():
    """Masaüstü yüzünü (enki_masaustu.py, PySide6) Enki ile birlikte aç; hub'a kendisi bağlanır."""
    import subprocess
    script = ROOT / "enki_masaustu.py"
    ok = subprocess.run([sys.executable, "-c", "import PySide6.QtWebEngineWidgets"],
                        capture_output=True).returncode == 0
    if not script.exists() or not ok:
        _status("yüz", False, "(pip install PySide6 — sonra Enki'yi yeniden aç)")
        return None
    p = subprocess.Popen([sys.executable, str(script)], cwd=ROOT,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _status("yüz", True, "(masaüstünde; menü çubuğundaki simgeden gizle/göster)")
    return p


def _status(name: str, ok: bool, note: str = "") -> None:
    print(f"  {name:<11} {'açık' if ok else YELLOW + 'kapalı' + RESET} {DIM}{note}{RESET}")


async def amain(args) -> None:
    # Ekran yazmak için temiz kalsın: her şey data/enki.log'a, ekrana sadece hatalar
    (ROOT / "data").mkdir(exist_ok=True)
    fh = logging.FileHandler(ROOT / "data" / "enki.log", encoding="utf-8")
    fh.setLevel(os.getenv("ENKI_LOG", "INFO"))
    sh = logging.StreamHandler()
    sh.setLevel(logging.ERROR)
    logging.basicConfig(level=logging.DEBUG, handlers=[fh, sh],
                        format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpcore", "websockets", "anthropic", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    cfg = Config()
    mem = Memory(cfg)
    llm = build_llm(cfg)
    console = ConsoleSpeaker(lambda: llm.name)
    voice = build_voice()
    speaks = voice is not None and not args.sessiz
    if speaks:
        from .tts import VoiceSpeaker
        local = VoiceSpeaker(voice, console)
    else:
        local = console

    muted = threading.Event()
    brain = Brain(cfg, llm, mem, console)
    print(f"{CYAN}Enki uyanıyor…{RESET}")

    hub = None
    speaker_ref = [None]  # hub, konuşmacıdan önce kuruluyor
    if not args.no_hub:
        from .hub import Hub
        try:
            hub = Hub(brain.post, interrupt=lambda: speaker_ref[0] and speaker_ref[0].interrupt())
            await hub.start()
            _status("gövde", True, f"telefon → {hub.phone_url()}")
        except Exception as e:  # port dolu: eski yuz_koprusu / enki_eller açık olabilir
            hub = None
            _status("gövde", False, f"(8765 meşgul mü? {e})")

    speaker = BodySpeaker(hub, local, console, lambda: brain.active_body, voice, muted)
    brain.speaker = speaker
    speaker_ref[0] = speaker
    loop = asyncio.get_running_loop()
    if hub:
        brain.fsm.subscribe(lambda a, s, b: (f := fsm_face(a, s, b)) and loop.create_task(hub.face(f)))

        def eyes_on_people(ev) -> None:  # boştayken: tanıyınca selam kırpışı, yabancıda dikkat
            if brain.fsm.q.name != "IDLE":
                return
            if isinstance(ev, FaceSeen):
                loop.create_task(hub.face("owner" if ev.who == "nehir" else "stranger"))
            elif isinstance(ev, FaceLeft):
                loop.create_task(hub.face("idle"))
        brain.observers.append(eyes_on_people)
    if os.getenv("ENKI_DEBUG_FSM"):
        brain.fsm.subscribe(lambda a, s, b: print(f"{DIM}[{a.name} --{s.name}--> {b.name}]{RESET}"))
    brain.fsm.subscribe(
        lambda a, s, b: b.name == "THINKING" and print(f"\r{DIM}…{RESET}", end="", flush=True)
    )

    vision = None
    if not args.no_cam:
        try:
            from .senses.vision import VisionSense
            vision = VisionSense(brain.post_threadsafe, ROOT, camera=args.kamera).start()
            VISION[0] = vision
            brain.eyes = vision.snapshot_jpeg
            brain.company = lambda: vision.others if vision.ok else 0
            _status("göz", True, "(/gor ile kontrol et)")
        except Exception as e:
            _status("göz", False, str(e))
    if not args.no_mic:
        try:
            from .senses.ear import EarSense
            voiceid = None
            try:
                from .senses.voiceid import VoiceID
                voiceid = VoiceID()
            except Exception as e:
                logging.getLogger("enki").warning("ses kimliği yok: %s", e)
            ear = EarSense(brain.post_threadsafe, muted, lambda: speaker.last_spoke, voiceid=voiceid,
                           gaze=vision.gaze_fraction if vision else None, whisper=args.whisper,
                           last_typed=lambda: TYPED[0], speaking=lambda: speaker.current,
                           interrupt=lambda: loop.call_soon_threadsafe(speaker.interrupt))
            print(f"  {DIM}kulak: Whisper yükleniyor…{RESET}")
            await loop.run_in_executor(None, ear.start)
            _status("kulak", True, f"{ear.stt.backend} · 'Enki' diye başla; yazarken sadece 'Enki' denince dinler")
            if ear.voiceid is not None and ear.voiceid.enrolled:
                _status("ses kimliği", True, f"{len(ear.voiceid.E)} örnek · eşik {ear.voiceid.threshold}")
            else:
                _status("ses kimliği", False, "(sesini öğret: enki_ses_kayit.command)")
        except Exception as e:
            _status("kulak", False, str(e)[:120])
    _status("ses", speaks, "" if speaks else "(--sessiz ya da edge-tts yok)")
    face_proc = None
    if hub and not args.yuzsuz:
        face_proc = launch_desktop_face()

    reader = asyncio.create_task(stdin_loop(brain, mem, speaker.interrupt))
    try:
        await brain.run()
    finally:
        reader.cancel()
        if face_proc and face_proc.poll() is None:
            face_proc.terminate()
        if vision:
            vision.stop()
        mem.close()


def main() -> None:
    ap = argparse.ArgumentParser(prog="enki")
    ap.add_argument("--sessiz", action="store_true", help="Enki sesli cevap vermez")
    ap.add_argument("--no-cam", action="store_true", help="kamera kapalı")
    ap.add_argument("--no-mic", action="store_true", help="mikrofon kapalı")
    ap.add_argument("--no-hub", action="store_true", help="telefon/yüz gövdeleri kapalı")
    ap.add_argument("--kamera", default=0, type=lambda s: int(s) if s.isdigit() else s)
    ap.add_argument("--whisper", default=os.getenv("ENKI_WHISPER", "auto"), help="auto/tiny/base/small/turbo")
    ap.add_argument("--kayit", action="store_true", help="yüzünü öğret ve çık")
    ap.add_argument("--sifirla", action="store_true", help="--kayit/--ses-kayit ile: eski kaydı sil")
    ap.add_argument("--ses-kayit", action="store_true", help="sesini öğret ve çık")
    ap.add_argument("--yuzsuz", action="store_true", help="masaüstü yüzünü açma")
    args = ap.parse_args()
    args.sessiz = args.sessiz or bool(os.getenv("ENKI_SESSIZ"))
    try:
        if args.ses_kayit:
            from .senses.voiceid import enroll as ses_enroll
            ses_enroll(reset=args.sifirla)
            return
        if args.kayit:
            from .senses.vision import enroll
            enroll(ROOT, args.kamera, reset=args.sifirla)
            return
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
