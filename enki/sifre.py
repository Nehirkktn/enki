"""Enki'nin şifresi: kapatmak, servisi kaldırmak ve kilidi elle açmak için.

Şifre düz metin saklanmaz: PBKDF2-SHA256 (200k tur, rastgele tuz) → data/sifre (chmod 600).
200k tur ≈ 0.1 sn: kilit ekranının klavye yakalayıcısı içinde doğrulanabilecek kadar hızlı,
kaba kuvvetle denemek için yeterince yavaş. Üstüne: 5 yanlış denemede 60 sn bekleme (dosyada tutulur,
yani Enki yeniden başlasa da sayaç sıfırlanmaz).
"""
from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIFRE = ROOT / "data" / "sifre"
DENEME = ROOT / "data" / "sifre_deneme.json"
TUR = 200_000
MAX_YANLIS, BEKLEME_S = 5, 60


def _uid() -> int:
    return os.getuid() if hasattr(os, "getuid") else 0


KAPATILDI = Path(f"/tmp/enki_kapatildi_{_uid()}")  # bekçi bu dosyayı görürse Enki'yi yeniden açmaz


def _hash(pw: str, salt: bytes, n: int = TUR) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, n)


def is_set(path: Path = SIFRE) -> bool:
    return path.exists()


def set_password(pw: str, path: Path = SIFRE) -> None:
    salt = secrets.token_bytes(16)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"salt": salt.hex(), "hash": _hash(pw, salt).hex(), "n": TUR}))
    tmp.chmod(0o600)
    tmp.replace(path)


def wait_left(deneme: Path = DENEME, now: float | None = None) -> float:
    """Çok yanlış denendiyse kaç sn beklenmeli (0 = deneyebilir)."""
    try:
        d = json.loads(deneme.read_text())
    except (OSError, ValueError):
        return 0.0
    return max(0.0, d.get("until", 0) - (now if now is not None else time.time()))


def verify(pw: str, path: Path = SIFRE, deneme: Path = DENEME, now: float | None = None) -> bool:
    """Doğru mu? Bekleme süresindeyken doğru şifre de reddedilir (kaba kuvveti yavaşlatmak için)."""
    now = time.time() if now is None else now
    if not path.exists() or wait_left(deneme, now) > 0:
        return False
    d = json.loads(path.read_text())
    ok = hmac.compare_digest(_hash(pw, bytes.fromhex(d["salt"]), d.get("n", TUR)).hex(), d["hash"])
    try:
        st = json.loads(deneme.read_text())
    except (OSError, ValueError):
        st = {}
    if ok:
        st = {}
    else:
        st["yanlis"] = st.get("yanlis", 0) + 1
        if st["yanlis"] >= MAX_YANLIS:
            st = {"yanlis": 0, "until": now + BEKLEME_S}
    try:
        deneme.write_text(json.dumps(st))
    except OSError:
        pass
    return ok


def _boot_id() -> str:
    try:
        return subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def mark_stopped() -> None:
    """Şifreyle kapatıldı: bekçi bu açılışta Enki'yi yeniden başlatmasın (Mac yeniden açılınca geçersiz)."""
    try:
        KAPATILDI.write_text(_boot_id())
    except OSError:
        pass


def cli_set() -> None:
    """python -m enki --sifre : şifre koy ya da değiştir."""
    if is_set():
        if wait_left() > 0:
            print(f"Çok fazla yanlış deneme. {wait_left():.0f} sn bekle.")
            return
        if not verify(getpass.getpass("Eski şifre: ")):
            print("Yanlış. Değiştirilmedi.")
            return
    while True:
        a = getpass.getpass("Yeni şifre (en az 4 karakter, yazarken görünmez): ")
        if len(a) < 4:
            print("Çok kısa.")
            continue
        if a != getpass.getpass("Tekrar: "):
            print("Uyuşmadı, baştan.")
            continue
        break
    set_password(a)
    print("✓ Şifre kaydedildi. Enki'yi kapatmak, kaldırmak ve kilidi elle açmak için bu lazım.")
    print("  Kilit ekranında: şifreyi yaz, Enter. (Mac'in klavye dili neyse onunla yaz.)")


def cli_check() -> int:
    """enki_kaldir.command için: 0 = doğru."""
    if not is_set():
        return 0
    if (w := wait_left()) > 0:
        print(f"Çok fazla yanlış deneme. {w:.0f} sn bekle.")
        return 1
    return 0 if verify(getpass.getpass("Enki şifresi: ")) else 1
