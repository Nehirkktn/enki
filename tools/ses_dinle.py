"""Enki ses seçimi: aynı cümleleri her adayda çalar, dosyaları voices/aday/ altına kaydeder.

  ~/.venv/bin/python tools/ses_dinle.py            # tüm hazır adaylar
  ~/.venv/bin/python tools/ses_dinle.py 2 4        # sadece 2. ve 4. aday
  ~/.venv/bin/python tools/ses_dinle.py ahmet      # Ahmet'i şekillendirme ızgarası (A–H)
  ~/.venv/bin/python tools/ses_dinle.py ahmet C F  # ızgaradan sadece C ve F
  ~/.venv/bin/python tools/ses_dinle.py h          # 2. tur: H etrafında ince ayar (H0–H7)
  ~/.venv/bin/python tools/ses_dinle.py h H2 H5    # 2. turdan sadece seçtiklerin
  ~/.venv/bin/python tools/ses_dinle.py x          # 3. tur: genç + enerjik + parlak (X1–X6)
  ~/.venv/bin/python tools/ses_dinle.py duygu X6          # aynı konuşma: düz vs TON (aynı kişi, tonlamalı)
  ~/.venv/bin/python tools/ses_dinle.py duygu X6 ton      # sadece ton (modlar: duz, ton, duygulu, guclu)
  ~/.venv/bin/python tools/ses_dinle.py duygu X6 ton laugh stern   # sadece bu duyguların cümleleri
  ~/.venv/bin/python tools/ses_dinle.py duygu X1 ton hiz+4          # taban hızını değiştir (X1'de +13)
  ~/.venv/bin/python tools/ses_dinle.py gulme X1 hiz+6              # gülme sesi adayları (G1–G15), cümleyle birlikte; sonuna G13 G14 yazarsan sadece onlar

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


# Duygu → ses: her cümle, beynin ürettiği duygu etiketine göre tabana göre kaydırılır.
# (hız farkı %, perde farkı Hz). Gözlerdeki [smug]/[blush]/[angry] etiketlerinin aynısı.
DUYGU = {
    "neutral": (0, 0),
    "excited": (+8, +8),
    "smug":    (-6, -6),
    "laugh":   (+5, +4),
    "blush":   (-2, +12),
    "stern":   (-12, -12),
}
DUYGU_GUCLU = {  # aynı yön, yaklaşık iki kat doz
    "neutral": (0, 0),
    "excited": (+16, +16),
    "smug":    (-14, -12),
    "laugh":   (+10, +10),
    "blush":   (-5, +22),
    "stern":   (-22, -20),
}

# TON: aynı kişi, sadece tonlama. Perde SABİT (perde değişince ses başka biri gibi geliyor).
# (hız farkı %, ses yüksekliği farkı %, parçalar arası duraklama ms)
TON = {  # hız: hepsi tabanda (gülmenin hızı beğenildi); duygu ses yüksekliği, duraklama ve konturdan
    "neutral":   (0,  0,   40),
    "excited":   (+3, +15, 0),    # yüksek, nefes almadan; hızda çok küçük artış
    "smug":      (0,  -5,  60),   # kısık, kısa duraklama
    "laugh":     (0,  +8,  20),   # oyuncu ton; gülmenin kendisi gözlerde ([laugh] animasyonu)
    "blush":     (0,  -12, 60),   # utangaç: kısık
    "stern":     (0,  +10, 70),   # sert: net, yüksek
}
KONTUR_HZ = 3  # cümle içi iniş-çıkış: ±3 Hz (kimliği değiştirmeyecek kadar küçük)

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


def _parcala(line: str) -> list[str]:
    """Cümleyi noktalama sınırlarından parçalar (noktalama parçada kalır)."""
    import re
    # Sadece cümle sonlarından böl: virgüldeki doğal kısa duruşu Ahmet zaten kendisi yapıyor,
    # her virgülde parçalamak gereksiz dikiş ve duraklama yaratıyordu.
    return [p.strip() for p in re.findall(r"[^.!?…]+[.!?…]*", line) if p.strip()]


def _kontur(parca: str, i: int, n: int) -> tuple[int, int]:
    """Cümle içi tonlama: düz cümlede ses başta yüksek, sonda iner (Türkçede doğal düşüş);
    soru sonunda kalkar, ünlemde enerji artar. (hız farkı, perde farkı)"""
    dp = round(KONTUR_HZ - 2 * KONTUR_HZ * i / max(n - 1, 1)) if n > 1 else 0
    dr = 0
    if parca.endswith("?"):
        dp += KONTUR_HZ + 2
    if parca.endswith("!"):
        dr += 5
    return dr, dp


def _kirp(pcm, sr: int, esik: int = 400, pay_ms: int = 12):
    """Baştaki/sondaki sessizliği at; konuşmanın kenarında küçük bir pay bırak."""
    import numpy as np
    yuksek = np.flatnonzero(np.abs(pcm) > esik)
    if not len(yuksek):
        return pcm
    pay = int(sr * pay_ms / 1000)
    return pcm[max(0, yuksek[0] - pay): yuksek[-1] + pay]


def _birlestir(parcalar: list[pathlib.Path], hedef: pathlib.Path, ara_ms: int = 60, fade_ms: int = 10) -> None:
    import wave
    import numpy as np
    with wave.open(str(parcalar[0]), "rb") as r:
        params = r.getparams()
    sr = params.framerate
    fade = int(sr * fade_ms / 1000)
    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
    out = []
    for k, p in enumerate(parcalar):
        with wave.open(str(p), "rb") as r:
            pcm = np.frombuffer(r.readframes(r.getnframes()), np.int16)
        pcm = _kirp(pcm, sr).astype(np.float32)
        if len(pcm) > 2 * fade:  # 10 ms yumuşak giriş/çıkış: geçişte çıtırtı olmasın
            pcm[:fade] *= ramp
            pcm[-fade:] *= ramp[::-1]
        out.append(pcm)
        if k < len(parcalar) - 1:
            out.append(np.zeros(int(sr * ara_ms / 1000), np.float32))
    with wave.open(str(hedef), "wb") as w:
        w.setparams(params)
        w.writeframes(np.concatenate(out).astype(np.int16).tobytes())


def _render_vol(path: pathlib.Path, rate: str, pitch: str, volume: str, line: str) -> bool:
    """EdgeEngine ses yüksekliği parametresini geçirmiyor; burada doğrudan edge_tts çağrılıyor."""
    if path.exists():
        return True
    import asyncio, tempfile
    import edge_tts
    try:
        with tempfile.TemporaryDirectory() as td:
            mp3 = pathlib.Path(td) / "a.mp3"
            asyncio.run(edge_tts.Communicate(line, AHMET, rate=rate, pitch=pitch, volume=volume).save(str(mp3)))
            T._mp3_to_wav(mp3, path)
        return True
    except Exception as e:
        print(f"   üretilemedi: {e}")
        return False


def _render_ton(wav: pathlib.Path, hiz: str, perde: str, efekt: str | None, line: str, tag: str) -> bool:
    if wav.exists():
        return True
    dr0, dv, ara = TON[tag]
    parcalar = _parcala(line)
    tmp = []
    for i, parca in enumerate(parcalar):
        dr, dp = _kontur(parca, i, len(parcalar))
        p = wav.with_name(f"{wav.stem}_p{i}.wav")
        if not _render_vol(p, _shift(hiz, dr0 + dr, "%"), _shift(perde, dp, "Hz"), f"{dv:+d}%", parca):
            return False
        tmp.append(p)
    _birlestir(tmp, wav, ara_ms=ara)
    for p in tmp:
        p.unlink()
    T.apply_effect(wav, efekt)
    return True


def _render_konturlu(wav: pathlib.Path, hiz: str, perde: str, efekt: str | None, line: str) -> bool:
    if wav.exists():
        return True
    parcalar = _parcala(line)
    tmp = []
    for i, parca in enumerate(parcalar):
        dr, dp = _kontur(parca, i, len(parcalar))
        p = wav.with_name(f"{wav.stem}_p{i}.wav")
        if not render(p, AHMET, _shift(hiz, dr, "%"), _shift(perde, dp, "Hz"), None, parca):
            return False
        tmp.append(p)
    _birlestir(tmp, wav)
    for p in tmp:
        p.unlink()
    T.apply_effect(wav, efekt)
    return True


# ---------------------------------------------------------------- gülme sesi adayları
# Metin adayları: Ahmet'in kendisi okuyor. Sentez adayları: tek bir "ha" hecesinden DSP ile kıkırdama.
GULME_METIN = {
    "G1": ("Heh heh.", "+20%"),
    "G2": ("Hı hı hı.", "+25%"),
    "G3": ("Ha ha!", "+15%"),
    "G4": ("Hah!", "+10%"),
}


def _wav_oku(path: pathlib.Path):
    import wave
    import numpy as np
    with wave.open(str(path), "rb") as r:
        return np.frombuffer(r.readframes(r.getnframes()), np.int16), r.getparams()


def _wav_yaz(path: pathlib.Path, pcm, params) -> None:
    import wave
    with wave.open(str(path), "wb") as w:
        w.setparams(params)
        w.writeframes(pcm.astype("int16").tobytes())


def _hizlandir(pcm, oran: float):
    """Yeniden örnekleme: oran>1 hem hızlandırır hem tizleştirir (kısa hecede doğal kıkırdama etkisi)."""
    import numpy as np
    n = int(len(pcm) / oran)
    return np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm.astype(np.float32))


def _kikirdama(out: pathlib.Path, hiz: str, perde: str, robot: bool) -> pathlib.Path | None:
    """'ha' hecesini 3 kez, her seferinde biraz daha kısa/alçak ve sessizleşerek dizer: ha-ha-ha düşüşü."""
    import numpy as np
    hece = out / "hece_ha.wav"
    if not render(hece, AHMET, hiz, perde, None, "Ha."):
        return None
    pcm, params = _wav_oku(hece)
    sr = params.framerate
    pcm = _kirp(pcm, sr, pay_ms=5)[: int(sr * 0.22)].astype(np.float32)   # tek kısa hece
    fade = int(sr * 0.03)
    pcm[-fade:] *= np.linspace(1, 0, fade)
    parca = []
    for k, (oran, kazanc) in enumerate([(1.12, 1.0), (1.0, 0.8), (0.92, 0.6)]):
        parca.append(_hizlandir(pcm, oran) * kazanc)
        parca.append(np.zeros(int(sr * 0.06), np.float32))
    son = out / f"kikirdama{'_robot' if robot else ''}.wav"
    _wav_yaz(son, np.concatenate(parca), params)
    T.apply_effect(son, "robot_hafif" if robot else "jarvis")
    return son


def _nefes(sr: int, ms: int = 260, seviye: float = 900.0, yumusaklik: float = 0.85):
    """Gülmenin sonundaki burundan nefes: alçak geçirgen gürültü, yumuşak zarf."""
    import numpy as np
    n = int(sr * ms / 1000)
    g = np.random.default_rng(7).normal(0, 1, n).astype(np.float32)
    g = T._one_pole(g, yumusaklik)                # hışırtıyı yumuşat (düşük değer = daha "h" gibi)
    zarf = np.sin(np.linspace(0, np.pi, n)) ** 2
    return g / (np.abs(g).max() or 1) * seviye * zarf


def _uzun_kikirdama(out: pathlib.Path, hiz: str, perde: str, ad: str, hece_metni: str,
                    adim: int, hece_ms: int, ara_ms: int, efekt: str,
                    sonum: float = 0.55, perde_dusus: float = 0.22, yavaslama: float = 0.6,
                    ara_nefes: bool = False, oynaklik: float = 0.03, h_sesi: bool = False,
                    zikzak: float = 0.0) -> pathlib.Path | None:
    """Daha uzun, insansı kıkırdama: hece sayısı, ritim düzensizliği, sönümlenme ve sonda nefes."""
    import numpy as np
    hece = out / f"hece_{hece_metni.strip('.').lower()}.wav"
    if not render(hece, AHMET, hiz, perde, None, hece_metni):
        return None
    pcm, params = _wav_oku(hece)
    sr = params.framerate
    pcm = _kirp(pcm, sr, pay_ms=5)[: int(sr * hece_ms / 1000)].astype(np.float32)
    fade = int(sr * 0.035)
    pcm[-fade:] *= np.linspace(1, 0, fade)
    rng = np.random.default_rng(3)
    parca = []
    for k in range(adim):
        t = k / max(adim - 1, 1)
        oran = 1.05 - perde_dusus * t + rng.uniform(-oynaklik, oynaklik)   # her hece biraz farklı
        oran += zikzak * (1 if k % 2 == 0 else -1)                         # gülmede perde iner-çıkar
        kazanc = (1.0 - sonum * t) * rng.uniform(0.88, 1.0)
        hece_k = _hizlandir(pcm, oran) * kazanc
        if oynaklik > 0.03:  # hece uzunluğu da oynasın: insan her "ha"yı aynı uzunlukta söylemez
            hece_k = hece_k[: int(len(hece_k) * rng.uniform(0.8, 1.0))]
            f = min(int(sr * 0.02), len(hece_k) // 4)
            hece_k[-f:] *= np.linspace(1, 0, f)
        if h_sesi:  # "ha"nın baştaki h'si: kırpmada kayboluyordu, o yüzden "a a a" gibi duyuluyordu
            hh = _nefes(sr, ms=55, seviye=1500.0 * kazanc, yumusaklik=0.35)
            hh[-int(sr * 0.01):] *= 0.5
            parca.append(hh)
        parca.append(hece_k)
        bosluk = ara_ms * (1 + yavaslama * t) * rng.uniform(0.8, 1.2)
        if ara_nefes and k in (1, adim - 2):  # arada kısa nefes alış: gülerken hava çekmek
            parca.append(np.zeros(int(sr * 0.03), np.float32))
            parca.append(_nefes(sr, ms=120, seviye=500.0))
        parca.append(np.zeros(int(sr * bosluk / 1000), np.float32))
    parca.append(_nefes(sr, ms=180, seviye=700.0))
    son = out / f"{ad}.wav"
    _wav_yaz(son, np.concatenate(parca), params)
    T.apply_effect(son, efekt)
    return son


def gulme(base_key: str, taban_hiz: str | None, sadece: set[str] | None = None) -> None:
    _, hiz, perde, efekt = IZGARA_X.get(base_key, IZGARA_X["X1"])
    hiz = taban_hiz or hiz
    out = ROOT / "voices" / "aday" / "gulme"
    out.mkdir(parents=True, exist_ok=True)
    tag, cumle = next(x for x in SAHNE if x[0] == "laugh")
    govde = out / f"cumle_{hiz}.wav"
    if not _render_ton(govde, hiz, perde, efekt, cumle, tag):
        return
    adaylar_ = []
    for kod, (metin, h) in GULME_METIN.items():
        w = out / f"{kod}.wav"
        if render(w, AHMET, h, perde, efekt, metin):
            adaylar_.append((kod, f"metin: {metin}", w))
    for kod, robot in (("G5", False), ("G6", True)):
        w = _kikirdama(out, hiz, perde, robot)
        if w:
            adaylar_.append((kod, "sentez kıkırdama" + (" + hafif robot" if robot else ""), w))
    uzunlar = [  # (kod, açıklama, hece, adım, hece ms, ara ms, efekt)
        ("G7", "uzun kıkırdama: 5 'ha', sönen ritim, sonda nefes (~1.8 sn)", "Ha.", 5, 240, 120, "jarvis"),
        ("G8", "hızlı 'he' kıkırdaması: 6 hece, sonda nefes (~1.5 sn)",       "He.", 6, 170, 80,  "jarvis"),
        ("G9", "G7 + hafif robot",                                           "Ha.", 5, 240, 120, "robot_hafif"),
    ]
    sonmeyen = dict(sonum=0.1, perde_dusus=0.06, yavaslama=0.15, oynaklik=0.06)
    uzunlar += [  # sönme yok: ses gücü ve perde sabit kalıyor, her hece biraz farklı
        ("G10", "G7 sönmeden, daha doğal oynaklık",            "Ha.", 5, 240, 115, "jarvis", sonmeyen),
        ("G11", "G8 sönmeden ('he-he' kıkırdama)",             "He.", 6, 170, 85,  "jarvis", sonmeyen),
        ("G12", "G10 + arada kısa nefes alışlar",              "Ha.", 5, 240, 115, "jarvis", {**sonmeyen, "ara_nefes": True}),
    ]
    nefesli = dict(sonum=0.15, perde_dusus=0.05, yavaslama=0.1, oynaklik=0.05, h_sesi=True, zikzak=0.05)
    uzunlar += [  # her hecenin başında duyulur bir "h", kısa ünlüler, iner-çıkar perde
        ("G13", "h'li 'ha-ha-ha', kısa ve kesik",              "Ha.", 5, 150, 70,  "jarvis", nefesli),
        ("G14", "h'li 'he-he-he' kıkırdama",                    "He.", 6, 130, 60,  "jarvis", nefesli),
        ("G15", "G13 + ortada nefes alış",                      "Ha.", 6, 150, 70,  "jarvis", {**nefesli, "ara_nefes": True}),
    ]
    for kod, etiket, hece_metni, adim, hms, ams, ef, *ek in uzunlar:
        w = _uzun_kikirdama(out, hiz, perde, kod, hece_metni, adim, hms, ams, ef, **(ek[0] if ek else {}))
        if w:
            adaylar_.append((kod, etiket, w))
    for kod, etiket, w in adaylar_:
        if sadece and kod not in sadece:
            continue
        print(f"\n▶ {kod}  {etiket}")
        birlesik = out / f"{kod}_cumle.wav"
        _birlestir([w, govde], birlesik, ara_ms=90)
        subprocess.run(["afplay", str(birlesik)])
    print(f"\nDosyalar: {out}")


MODLAR = {
    "duz":     ("DÜZ (her cümle aynı ton)", None, False),
    "duygulu": ("DUYGULU (cümle cümle ton, hafif)", DUYGU, False),
    "guclu":   ("GÜÇLÜ (iki kat duygu + cümle içi tonlama)", DUYGU_GUCLU, True),
    "ton":     ("TON (aynı kişi; hız, ses yüksekliği, duraklama, küçük iniş-çıkış)", TON, "ton"),
}


def duygu(base_key: str, modlar: list[str], sadece: set[str] | None = None, taban_hiz: str | None = None) -> None:
    """Önce bütün cümleleri üret, sonra tek parça halinde çal: gerçek Enki de bir sonraki cümleyi
    o konuşurken hazırlıyor (voice.py pipeline), yani cümleler arasında üretim beklemesi olmayacak."""
    _, hiz, perde, efekt = IZGARA_X.get(base_key, IZGARA_X["X1"])
    hiz = taban_hiz or hiz
    out = ROOT / "voices" / "aday" / "duygu" / f"{base_key}_hiz{hiz}"
    out.mkdir(parents=True, exist_ok=True)
    for mod in modlar:
        baslik, tablo, kontur = MODLAR[mod]
        print(f"\n▶ {baslik}   taban: {base_key}, hız {hiz}")
        print("   hazırlanıyor...", flush=True)
        parcalar = []
        for i, (tag, line) in enumerate(SAHNE, 1):
            if sadece and not any(tag.startswith(x) for x in sadece):
                continue
            wav = out / f"{mod}{'6' if kontur == 'ton' else ''}_{i}.wav"
            if kontur == "ton":
                ok = _render_ton(wav, hiz, perde, efekt, line, tag)
            else:
                dr, dp = tablo.get(tag, tablo.get("laugh", (0, 0))) if tablo else (0, 0)
                h, p = _shift(hiz, dr, "%"), _shift(perde, dp, "Hz")
                ok = _render_konturlu(wav, h, p, efekt, line) if kontur else render(wav, AHMET, h, p, efekt, line)
            if not ok:
                return
            parcalar.append(wav)
            print(f"   [{tag if tablo else '-':7}] {line}")
        sahne = out / f"{mod}_sahne.wav"
        _birlestir(parcalar, sahne, ara_ms=180)  # cümleler arası doğal nefes payı
        subprocess.run(["afplay", str(sahne)])
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
        rest = args[1:]
        base = next((a.upper() for a in rest if a.upper() in IZGARA_X), "X1")
        modlar = [a.lower() for a in rest if a.lower() in MODLAR] or ["duz", "ton"]
        sadece = {a.lower() for a in rest if a.lower() in {"excited", "smug", "laugh", "blush", "stern"}}
        import re
        hz = next((m.group(1) for a in rest if (m := re.fullmatch(r"hiz([+-]?\d+)", a.lower()))), None)
        duygu(base, modlar, sadece or None, f"{int(hz):+d}%" if hz is not None else None)
    elif mode == "gulme":
        import re
        rest = args[1:]
        base = next((a.upper() for a in rest if a.upper() in IZGARA_X), "X1")
        hz = next((m.group(1) for a in rest if (m := re.fullmatch(r"hiz([+-]?\d+)", a.lower()))), None)
        sec = {a.upper() for a in rest if re.fullmatch(r"G\d+", a.upper())}
        gulme(base, f"{int(hz):+d}%" if hz is not None else None, sec or None)
    elif mode == "x":
        izgara({a.upper() for a in args[1:]}, IZGARA_X, [1, 2])
    else:
        adaylar({int(a) for a in args})


if __name__ == "__main__":
    main()
