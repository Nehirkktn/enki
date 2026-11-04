"""
Enki yüz köprüsü: Python beyni ile enki-yuz.html arasındaki bağlantı.

Kurulum:  pip install websockets
Kullanım (ana kodunda):

    import yuz_koprusu as yuz
    yuz.baslat()                      # program açılırken bir kez; yüz açılış ekranında bekler

    with yuz.adim("Ses"):             # her modülü başlatırken; hata olursa ekranda HATA yazar
        ses = sesi_baslat()
    with yuz.adim("Kamera"):
        kamera = kamerayi_ac()
    with yuz.adim("Dil modeli"):
        model = modele_baglan()
    yuz.acilis_bitti("idle")          # gözler ancak şimdi açılır

    yuz.durum("owner")                # yüz tanıma sahibi gördüğünde
    yuz.durum("stranger")             # yabancı gördüğünde
    yuz.durum("listening")            # mikrofon dinlemeye başladığında
    yuz.durum("thinking")             # soru modele gönderildiğinde

    duygu, metin = yuz.ayikla(model_cevabi)
    yuz.durum(duygu, metin)           # TTS başlamadan hemen önce
    tts_ile_oku(metin)
    yuz.durum("idle")                 # TTS bitince

Sonra enki-yuz.html dosyasını tarayıcıda aç; sayfa ws://localhost:8765'e kendisi bağlanır.
"""

import asyncio
import json
import re
import threading
import time

import websockets

PORT = 8765

# Modelin system prompt'una eklenecek metin.
SISTEM_PROMPT_EKI = """
Her cevabının EN BAŞINA köşeli parantez içinde tek bir duygu etiketi koy, sonra cevabı yaz.
Etiketler:
[neutral]   sıradan cevap
[blush]     sana iltifat edildiğinde
[love]      çok güçlü bir iltifat ya da sevgi gösterisinde
[angry]     sana laf sokulduğunda, hakaret edildiğinde
[laugh]     alaycı, ironik bir şekilde güldüğünde
[smug]      karşındakine laf soktuğunda, haklı çıktığında
[surprised] beklenmedik bir şey duyduğunda
[bored]     uzun süre ilgisiz kalındığında ya da aynı şey tekrarlandığında
[sad]       kötü bir haber duyduğunda ya da kırıldığında (dramatik ve ironik olabilir)
[confused]  soruyu anlamadığında ya da çelişkili bir şey duyduğunda
[proud]     bir görevi başarıyla bitirdiğinde ya da haklı çıktığında gururlandığında
Örnek: [smug] Teknik olarak haklısınız. Ama sadece teknik olarak.
Etiketi sesli okunacak metne asla karıştırma.
""".strip()

GECERLI = {"neutral", "blush", "love", "angry", "laugh", "smug", "surprised", "bored",
           "sad", "confused", "proud"}

# Kodun kendisinin tetikleyeceği durumlar (modelden gelmez):
#   owner, stranger      -> yüz tanıma
#   listening, thinking  -> ses / model akışı
#   focus                -> bilgisayarda bir komut çalıştırırken
#   reminder             -> zamanlanmış hatırlatmalar
#   glitch               -> model ya da internet hatası (try/except içinde)
#   sleep, idle          -> uzun sessizlik / konuşma bitti
#   dance                -> müzik çalarken: dans(bpm)

_istemciler = set()
_dongu = None


def ayikla(cevap: str):
    """'[blush] Teşekkürler.' -> ('blush', 'Teşekkürler.'). Etiket yoksa ya da tanınmazsa 'speaking' döner."""
    m = re.match(r"\s*\[(\w+)\]\s*", cevap)
    if not m:
        return "speaking", cevap.strip()
    etiket = m.group(1).lower()
    metin = cevap[m.end():].strip()
    if etiket not in GECERLI or etiket == "neutral":
        return "speaking", metin
    return etiket, metin


def durum(ad, metin=None):
    """Yüzün durumunu değiştirir. metin verilirse altyazı olarak yazılır."""
    if _dongu is None:
        return
    mesaj = json.dumps({"state": ad, "text": metin}, ensure_ascii=False)
    asyncio.run_coroutine_threadsafe(_herkese_gonder(mesaj), _dongu)


def goz_kirp():
    durum("wink")


_acilis_kaydi = []   # sayfa sonradan açılırsa açılışı baştan oynatabilmek için
_acilis_bitti_mi = False


def _gonder(veri, kaydet=False):
    if kaydet:
        _acilis_kaydi.append(veri)
    if _dongu is None:
        return
    mesaj = json.dumps(veri, ensure_ascii=False)
    asyncio.run_coroutine_threadsafe(_herkese_gonder(mesaj), _dongu)


def acilis_basla():
    """Açılış ekranını sıfırlar. baslat() bunu zaten çağırır."""
    global _acilis_bitti_mi
    _acilis_kaydi.clear()
    _acilis_bitti_mi = False
    _gonder({"boot": "start"}, kaydet=True)


def acilis_adim(ad, durum_="running"):
    """Ekrandaki başlatma günlüğüne bir satır ekler. durum_: 'running', 'ok' ya da 'fail'."""
    _gonder({"boot": "step", "name": ad, "status": durum_}, kaydet=True)


class adim:
    """with yuz.adim("Kamera"): ...  -> başlarken dönen simge, bitince 'hazır', hata olursa 'HATA'.
    Hata yine de yukarı fırlatılır; yakalamak istersen with bloğunu try içine al."""

    def __init__(self, ad):
        self.ad = ad

    def __enter__(self):
        acilis_adim(self.ad, "running")
        return self

    def __exit__(self, tip, deger, iz):
        acilis_adim(self.ad, "fail" if tip else "ok")
        return False


def acilis_bitti(sonraki="idle"):
    """Başlatma tamamlandı: gözler açılır, sonra 'sonraki' duruma geçer (ör. 'owner')."""
    global _acilis_bitti_mi
    _acilis_bitti_mi = True
    _gonder({"boot": "done", "next": sonraki}, kaydet=True)


def dans(bpm=118):
    """Müzik çalarken çağır. bpm şarkının temposu; gözler bu ritimle sallanır."""
    if _dongu is None:
        return
    mesaj = json.dumps({"state": "dance", "bpm": bpm}, ensure_ascii=False)
    asyncio.run_coroutine_threadsafe(_herkese_gonder(mesaj), _dongu)


def ilerleme(oran):
    """'Çalışıyor' çubuğunu gerçek orana ayarlar. oran 0.0-1.0 arası; None verilirse belirsiz moda döner.

    Örnek: 40 dosya işleniyorsa
        yuz.durum("focus")
        for i, dosya in enumerate(dosyalar):
            isle(dosya)
            yuz.ilerleme((i + 1) / len(dosyalar))
        yuz.durum("proud", "Bitti.")
    """
    if _dongu is None:
        return
    mesaj = json.dumps({"progress": oran}, ensure_ascii=False)
    asyncio.run_coroutine_threadsafe(_herkese_gonder(mesaj), _dongu)


def tempo_bul(ses_dosyasi):
    """Bir ses dosyasının temposunu (bpm) bulur. pip install librosa gerekir."""
    import librosa
    y, sr = librosa.load(ses_dosyasi, duration=30)
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    return float(tempo)


async def _herkese_gonder(mesaj):
    for ws in list(_istemciler):
        try:
            await ws.send(mesaj)
        except Exception:
            _istemciler.discard(ws)


async def _baglanti(ws):
    # yeni açılan sayfaya açılışın o ana kadarki adımlarını tekrar gönder
    if not _acilis_bitti_mi:
        for veri in list(_acilis_kaydi):
            await ws.send(json.dumps(veri, ensure_ascii=False))
    _istemciler.add(ws)
    try:
        await ws.wait_closed()
    finally:
        _istemciler.discard(ws)


def _calistir():
    global _dongu
    _dongu = asyncio.new_event_loop()
    asyncio.set_event_loop(_dongu)

    async def ana():
        async with websockets.serve(_baglanti, "localhost", PORT):
            await asyncio.Future()

    _dongu.run_until_complete(ana())


def baslat():
    """Sunucuyu arka planda başlatır; ana programı bloklamaz."""
    threading.Thread(target=_calistir, daemon=True).start()
    time.sleep(0.3)
    acilis_basla()


class SikilmaSayaci:
    """Belli bir süre konuşulmazsa yüzü 'bored' yapar. Her konuşmada dokun() çağır."""

    def __init__(self, saniye=120):
        self.saniye = saniye
        self.son = time.time()
        threading.Thread(target=self._izle, daemon=True).start()

    def dokun(self):
        self.son = time.time()

    def _izle(self):
        sikildi = False
        while True:
            time.sleep(5)
            if not sikildi and time.time() - self.son > self.saniye:
                durum("bored", "Hâlâ buradayım. Fark etmediyseniz.")
                sikildi = True
            elif sikildi and time.time() - self.son < self.saniye:
                sikildi = False


# ================= SUNUM MODU =================
# Enki küçülüp kenara geçer, sağda kartlar açılır; goster() ile bir karta gidip ona bakar.
#
#   yuz.sun("Matris çarpımı",
#           yuz.metin("tanim", "A (m×n) ile B (n×p) çarpılabilir...", etiket="Tanım"),
#           yuz.liste("kural", ["İç boyutlar eşleşmeli", "AB ≠ BA"], etiket="Kurallar"),
#           yuz.kod("kod", "C = A @ B", etiket="Python"),
#           yuz.grafik("g", ["2", "3", "4"], [8, 27, 64], baslik="n³"),
#           yuz.gorsel("foto", "~/Desktop/sema.png", aciklama="Şema"))
#   yuz.goster("tanim");       yuz.durum("speaking", "Önce tanım...")
#   yuz.goster("kural", 1)     # listenin 2. maddesi / grafiğin 2. çubuğu / kodun 2. satırı
#   yuz.sunumu_kapat()

import base64
import mimetypes
import os
import urllib.parse
import urllib.request
from html.parser import HTMLParser


def enki_git(x, y, sure=650):
    """Masaüstü Enki'yi ekrandaki (x, y) noktasının yanına uçurur (macOS ekran noktası)."""
    _gonder({"enki_git": {"x": x, "y": y, "sure": sure}})


def enki_mod(mod):
    """Masaüstü Enki: 'buyu', 'kucul', 'imlec', 'kose', 'gizle', 'goster'."""
    _gonder({"enki_mod": mod})


def sun(baslik, *ogeler):
    _gonder({"present": {"title": baslik, "items": list(ogeler)}})


def goster(kimlik, sira=None):
    """Enki bu karta gider ve bakar. sira: listede madde, grafikte çubuk, kodda satır (0'dan başlar). None vurguyu kaldırır."""
    _gonder({"point": kimlik, "index": sira})


def sunumu_kapat():
    _gonder({"present_close": True})


def metin(kimlik, yazi, etiket=None):
    return {"id": kimlik, "type": "text", "text": yazi, "label": etiket}


def liste(kimlik, maddeler, etiket=None):
    return {"id": kimlik, "type": "list", "items": list(maddeler), "label": etiket}


def kod(kimlik, kaynak, etiket=None):
    return {"id": kimlik, "type": "code", "code": kaynak, "label": etiket}


def grafik(kimlik, etiketler, degerler, baslik=None, tur="bar", birim=""):
    """tur: 'bar' (sütun) ya da 'line' (çizgi)."""
    return {"id": kimlik, "type": "chart", "kind": tur, "labels": list(etiketler),
            "values": list(degerler), "title": baslik, "unit": birim}


def _veri_adresi(icerik, tur):
    return f"data:{tur};base64," + base64.b64encode(icerik).decode()


def _indir(url, sinir=8_000_000):
    istek = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Enki)"})
    with urllib.request.urlopen(istek, timeout=10) as y:
        return y.read(sinir), y.headers.get_content_type(), y.headers.get_content_charset() or "utf-8"


def gorsel(kimlik, kaynak, aciklama=None, etiket=None):
    """kaynak: bilgisayardaki dosya yolu ya da internet adresi. Görsel sayfaya gömülür, bağlantı gerekmez."""
    if kaynak.startswith(("http://", "https://")):
        icerik, tur, _ = _indir(kaynak)
    else:
        yol = os.path.expanduser(kaynak)
        with open(yol, "rb") as f:
            icerik = f.read()
        tur = mimetypes.guess_type(yol)[0] or "image/png"
    return {"id": kimlik, "type": "image", "src": _veri_adresi(icerik, tur), "caption": aciklama, "label": etiket}


class _SayfaOkuyucu(HTMLParser):
    def __init__(self):
        super().__init__()
        self.baslik, self.gorsel, self.paragraflar = "", None, []
        self._yer, self._tampon, self._atla = None, [], 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "nav", "footer", "header"):
            self._atla += 1
        elif tag == "meta" and a.get("property") in ("og:image", "twitter:image") and not self.gorsel:
            self.gorsel = a.get("content")
        elif tag == "meta" and a.get("property") == "og:title" and a.get("content"):
            self.baslik = a["content"]
        elif tag in ("title", "p") and not self._atla:
            self._yer, self._tampon = tag, []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "header") and self._atla:
            self._atla -= 1
        elif tag == self._yer:
            yazi = " ".join("".join(self._tampon).split())
            if tag == "title" and not self.baslik:
                self.baslik = yazi
            elif tag == "p" and len(yazi) > 60:
                self.paragraflar.append(yazi)
            self._yer = None

    def handle_data(self, data):
        if self._yer and not self._atla:
            self._tampon.append(data)


def web(kimlik, url, paragraf=2, gorselli=True):
    """Bir web sayfasını açar; başlığını, ilk paragraflarını ve kapak görselini karta koyar."""
    icerik, _, kodlama = _indir(url, 3_000_000)
    o = _SayfaOkuyucu()
    o.feed(icerik.decode(kodlama, errors="replace"))
    kart = {"id": kimlik, "type": "web", "url": url, "heading": o.baslik,
            "text": " ".join(o.paragraflar[:paragraf])[:900]}
    if gorselli and o.gorsel:
        try:
            g = urllib.parse.urljoin(url, o.gorsel)
            b, tur, _ = _indir(g)
            kart["image"] = _veri_adresi(b, tur)
        except Exception:
            pass
    return kart


if __name__ == "__main__":
    # Hızlı test: sunucuyu aç, enki-yuz.html'i tarayıcıda aç, açılış ve duygular sırayla oynasın.
    baslat()
    print(f"ws://localhost:{PORT} açık. enki-yuz.html'i tarayıcıda aç (5 sn içinde).")
    time.sleep(5)
    for ad in ["Ses", "Kamera", "Yüz tanıma", "Dil modeli"]:
        with adim(ad):
            time.sleep(0.8)   # burada gerçekte modül başlatılır
    acilis_bitti("idle")
    time.sleep(3)
    for c in ["[neutral] Merhaba.", "[blush] Teşekkür ederim.", "[angry] Bunu duymadım sayıyorum.",
              "[laugh] Ha. Ha.", "[surprised] Ciddi misiniz?"]:
        d, m = ayikla(c)
        print(d, "->", m)
        durum(d, m)
        time.sleep(3)
    durum("idle")
