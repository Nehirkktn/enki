"""Gövde merkezi: tek beyin, çok gövde.

Her gövde (Mac'teki yüz, telefon, ileride oda node'ları / ESP32) aynı WebSocket'e bağlanır:
    ws://<mac-ip>:8765            HTTP gövde sayfaları: http://<mac-ip>:8766/telefon.html?k=<anahtar>

Gövde → beyin                               Beyin → gövdeler
  {"hello": {"body": "telefon",               {"state": "thinking"|"blush"|..., "text": "..."}  (göz, herkese)
             "caps": ["face","text","speaker"],{"say": "...", "audio": "<base64 wav>"}          (sadece konuşulan gövdeye)
             "key": "<anahtar>"}}              {"welcome": {"body": ..., "bodies": [...]}}
  {"said": "metin"}       → UserSaid
  {"overheard": "metin"}  → Overheard
  {"seen": "nehir"|"yabanci"} / {"left": "nehir"} → FaceSeen / FaceLeft
  {"kapat": "<şifre>"}    → sadece bu Mac'ten (localhost): şifre doğruysa Enki kapanır → {"kapat": true|false}

Eski enki-yuz.html / enki_masaustu.py `hello` göndermez: localhost'tan gelen her bağlantı
"yuz" adlı, sadece göz gösteren gövde sayılır → değişiklik gerekmeden çalışırlar.
Yurt Wi-Fi'ı herkese açık: localhost dışındaki gövdeler anahtar olmadan hiçbir şey yapamaz.
"""
from __future__ import annotations

import asyncio
import base64
import functools
import http.server
import io
import json
import logging
import secrets
import socket
import threading
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from .brain import Event, FaceLeft, FaceSeen, Overheard, UserSaid

log = logging.getLogger("enki.hub")

ROOT = Path(__file__).resolve().parents[1]
BODIES_DIR = ROOT / "bodies"
KEY_FILE = ROOT / "data" / "govde_anahtari"
LOCAL = {"127.0.0.1", "::1", "localhost"}


@dataclass(slots=True)
class Body:
    name: str
    caps: set[str] = field(default_factory=lambda: {"face"})
    trusted: bool = False


def load_key(path: Path = KEY_FILE) -> str:
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    k = secrets.token_urlsafe(9)
    path.write_text(k)
    path.chmod(0o600)
    return k


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # rota sorgusu, paket gitmez
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def wav_b64(pcm: np.ndarray, sr: int) -> str:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(np.clip(pcm, -32767, 32767).astype(np.int16).tobytes())
    return base64.b64encode(buf.getvalue()).decode()


def handle(body: Body, msg: dict, key: str) -> Event | None:
    """Gövdeden gelen tek mesaj → (gövde durumunu günceller) → beyin olayı. Saf: soket yok, test edilebilir."""
    if hello := msg.get("hello"):
        if body.trusted or secrets.compare_digest(str(hello.get("key", "")), key):
            body.trusted = True
            body.name = str(hello.get("body") or body.name)[:32]
            body.caps = set(hello.get("caps") or ["face"])
        return None
    if not body.trusted:
        return None
    if t := str(msg.get("said") or "").strip():
        return UserSaid(t[:2000], body.name)
    if t := str(msg.get("overheard") or "").strip():
        return Overheard(t[:2000], body.name)
    if who := msg.get("seen"):
        return FaceSeen(str(who), body.name)
    if who := msg.get("left"):
        return FaceLeft(str(who), body.name)
    return None


def _stop_cmd(text: str) -> bool:
    from .senses.ear import stop_command
    return stop_command(text)


class Hub:
    def __init__(self, post: Callable[[Event], None], host: str = "0.0.0.0", port: int = 8765,
                 http_port: int = 8766, interrupt: Callable[[], None] = lambda: None, on_kapat=None):
        self.post, self.host, self.port, self.http_port = post, host, port, http_port
        self.interrupt = interrupt
        self.on_kapat = on_kapat  # async (şifre) -> bool
        self.key = load_key()
        self.bodies: dict[object, Body] = {}   # websocket -> Body
        self._face: dict = {"state": "idle"}

    # ---------------------------------------------------------------- yaşam döngüsü
    async def start(self) -> None:
        import websockets
        self._server = await websockets.serve(self._conn, self.host, self.port)
        handler = functools.partial(_QuietHandler, directory=str(BODIES_DIR))
        httpd = http.server.ThreadingHTTPServer((self.host, self.http_port), handler)
        threading.Thread(target=httpd.serve_forever, name="enki-http", daemon=True).start()

    def phone_url(self) -> str:
        """Mac'in .local adı (Bonjour): IP değişse de link çalışır. IP'li yedek de loglanır."""
        host = socket.gethostname()
        host = host if host.endswith(".local") else host + ".local"
        log.info("telefon (IP ile): http://%s:%d/telefon.html?k=%s", lan_ip(), self.http_port, self.key)
        return f"http://{host}:{self.http_port}/telefon.html?k={self.key}"

    async def _conn(self, ws) -> None:
        ip = (ws.remote_address or ("?",))[0]
        local = ip in LOCAL
        body = Body("yuz" if local else f"misafir@{ip}", trusted=local)
        self.bodies[ws] = body
        try:
            await ws.send(json.dumps(self._face, ensure_ascii=False))
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if isinstance(msg, dict) and "kapat" in msg:
                    # ağdan gelen kapatma isteği hiç denenmez: yurt Wi-Fi'ında şifre tahmini olmasın
                    ok = bool(local and self.on_kapat and await self.on_kapat(str(msg["kapat"])))
                    log.info("kapatma isteği (%s): %s", body.name, "doğru" if ok else "reddedildi")
                    await ws.send(json.dumps({"kapat": ok}))
                    continue
                was = body.trusted, body.name
                ev = handle(body, msg, self.key) if isinstance(msg, dict) else None
                if (body.trusted, body.name) != was:
                    log.info("gövde bağlandı: %s %s", body.name, sorted(body.caps))
                    await ws.send(json.dumps({"welcome": {"body": body.name, "bodies": self.names()}}))
                if ev is not None:
                    if isinstance(ev, UserSaid) and _stop_cmd(ev.text):
                        self.interrupt()  # telefondan "enki dur" yazınca susar
                        continue
                    self.post(ev)
                elif body.trusted and msg.get("stop"):
                    self.interrupt()
        except Exception as e:
            log.debug("bağlantı koptu %s: %s", body.name, e)
        finally:
            self.bodies.pop(ws, None)

    # ---------------------------------------------------------------- beyin → gövdeler
    def names(self) -> list[str]:
        return sorted({b.name for b in self.bodies.values() if b.trusted})

    def has(self, name: str, cap: str) -> bool:
        return any(b.name == name and cap in b.caps for b in self.bodies.values())

    async def _send(self, msg: dict, pred: Callable[[Body], bool]) -> None:
        data = json.dumps(msg, ensure_ascii=False)
        for ws, b in list(self.bodies.items()):
            if b.trusted and pred(b):
                try:
                    await ws.send(data)
                except Exception:
                    self.bodies.pop(ws, None)

    async def face(self, state: str, text: str | None = None) -> None:
        self._face = {"state": state, "text": text}
        await self._send(self._face, lambda b: "face" in b.caps)

    async def send_to(self, name: str, msg: dict) -> None:
        await self._send(msg, lambda b: b.name == name)


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a) -> None:
        pass
