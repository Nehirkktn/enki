"""Beyin ↔ eller (enki_eller.py) köprüsü: bilgisayarda iş isteyen cümleler araç döngüsüne gider.

- Misafir sesi ([MİSAFİR SESİ]) asla eller kullanamaz.
- Onay gereken işler (dosya yazma, taşıma, komut, AppleScript, fare/klavye) Enki'nin sesiyle sorulur;
  Nehir'in bir sonraki cümlesi (sesle, terminalde ya da telefonda) cevap sayılır. 45 sn cevap yoksa: hayır.
- Eller kendi yüz durumlarını (focus, thinking...) hub üzerinden gözlere gönderir.
"""
from __future__ import annotations

import asyncio
import logging
import re
import sys
from pathlib import Path

log = logging.getLogger("enki.eller")
ROOT = Path(__file__).resolve().parents[1]

# Bilgisayarda iş isteği gibi duran cümleler (yanlış pozitif zararsız: eller de sohbet edebilir)
HANDS_RE = re.compile(
    r"(\baç\b|\baçar|\baçsana|\baçabilir|\bkapat|\bbul\b|\bbulur|\bara\b|\barar mısın|\blistele|"
    r"\bgöster|\boku\b|\bokur musun|\bkaydet|\btaşı|\bçöpe|\bsil\b|\bçal\b|\bçalar|\bdurdur|"
    r"\bsesi (aç|kıs|yükselt|azalt)|ekran görüntüsü|\bklasör|\bdosya|\buygulama|\bsafari|\bchrome|"
    r"\byoutube|\bspotify|\bmüzik|\bföy|\bpdf\b|\bmasaüst|\bindir|\bhatırlatıcı|\btakvim|\bnot al)", re.I)
YES_RE = re.compile(r"\b(evet|yap|tamam|olur|onaylıyorum|onayla|aynen|tabii)\b", re.I)


def wants_hands(text: str) -> bool:
    return not text.startswith("[MİSAFİR SESİ]") and bool(HANDS_RE.search(text))


class _FaceAdapter:
    """enki_eller'in beklediği yuz_koprusu arayüzü → hub.face (thread-safe)."""

    def __init__(self, loop, hub):
        self.loop, self.hub = loop, hub
        from yuz_koprusu import ayikla  # etiket ayrıştırma aynı kalsın
        self.ayikla = ayikla

    def durum(self, ad, metin=None):
        if self.hub:
            asyncio.run_coroutine_threadsafe(self.hub.face(ad, metin), self.loop)


class Hands:
    def __init__(self, loop: asyncio.AbstractEventLoop, ask, hub=None):
        """ask: async (soru) -> bool  — Nehir'e sesli sor, cevabı bekle."""
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import enki_eller  # noqa: E402

        self.mod, self.loop, self.ask = enki_eller, loop, ask
        try:
            enki_eller.yuz = _FaceAdapter(loop, hub)
        except Exception:
            enki_eller.yuz = None

    def _onay(self, soru: str) -> bool:  # eller thread'inden çağrılır
        fut = asyncio.run_coroutine_threadsafe(self.ask(soru), self.loop)
        try:
            return bool(fut.result(timeout=60))
        except Exception:
            return False

    async def run(self, text: str, context: str) -> str:
        istek = f"{context}\n\nNehir şunu istiyor: {text}"
        log.info("eller: %s", text)
        return await self.loop.run_in_executor(
            None, lambda: self.mod.gorev(istek, konus=lambda m: None, onay=self._onay))
