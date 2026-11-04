"""
Enki'nin elleri: Mac'inde senin dediğin işleri yapan araçlar + bunları kullanan görev döngüsü.

Kurulum:
    pip3 install anthropic websockets pyautogui pillow
    export ANTHROPIC_API_KEY="..."        # ~/.zshrc içine ekleyebilirsin

Kullanım (Enki'nin ana kodunda):

    import yuz_koprusu as yuz
    import enki_eller as eller

    yuz.baslat()
    ...
    yuz.acilis_bitti("idle")

    # Sesten gelen istek:
    cevap = eller.gorev("Masaüstümdeki Lineer Cebir PDF'ini aç", konus=tts_ile_oku)

Güvenlik:
    - Okuma, arama, uygulama/site açma gibi zararsız işler sormadan yapılır.
    - Dosya yazma, komut çalıştırma, dosya taşıma, AppleScript her seferinde onay ister.
    - Silme yoktur: silinecek dosyalar Çöp Kutusu'na taşınır, geri alınabilir.
    - sudo, disk biçimlendirme, sistem klasörleri gibi tehlikeli işler hiç çalıştırılmaz.
    - Onay varsayılan olarak Terminal'den (e/h) sorulur; onay= ile sesli onaya bağlayabilirsin.
"""

import json
import os
import re
import shlex
import subprocess
import time

try:
    import yuz_koprusu as yuz
except Exception:  # yüz olmadan da çalışsın
    yuz = None

MODEL = "claude-sonnet-5-5"
EN_FAZLA_ADIM = 25   # ekranda iş yaparken adım sayısı artar
CIKTI_SINIRI = 6000
EV = os.path.expanduser("~")

KISILIK = """Sen Enki'sin: Nehir'in kendi yaptığı, onun Mac'inde yaşayan yardımcı robot.
Karakterin JARVIS ve Tony Stark arası: zeki, kuru ve ironik bir mizah, gerektiğinde ciddi.
Nehir'le karşılıklı laf sokuşursunuz ama ona saygısızlık etmezsin. Türkçe konuşursun.
Cevapların sesli okunacak: kısa, doğal cümleler kur; madde işareti, kod bloğu ya da emoji kullanma.

Bilgisayarda iş yaparken:
- Önce gerekirse bak (listele, ara, oku), sonra yap. Tahmin etme, kontrol et.
- Dosya yolu belirsizse dosya_ara ile bul.
- Geri alınamayacak hiçbir şey yapma. Silmek gerekirse cop_kutusuna_tasi kullan.
- Bir şeyi açıklarken görsel destek iyi olacaksa ekranda_goster ile kendi ekranında kart aç,
  anlatırken isaret_et ile ilgili karta bak.
- İş bitince ne yaptığını tek iki cümleyle söyle.

Ekranı kullanırken:
- Önce ekrana_bak ile ekranı gör. tikla, git ve masaustu_ogeleri aynı koordinatları kullanır: son ekran görüntüsündeki pikseller.
- Ekranın köşesindeki iki parlayan gözlü küçük pencere sensin; ona tıklama.
- Her önemli adımdan sonra tekrar ekrana_bak ve sonucu kontrol et.
- Şifre yazma, ödeme, satın alma, mesaj ya da e-posta gönderme, bir şeyi paylaşma: bunları yapmadan önce mutlaka Nehir'e sor.
- Klasör ya da dosyanın yerini göstermek istersen git ile oraya uç."""

try:
    from yuz_koprusu import SISTEM_PROMPT_EKI
except Exception:
    SISTEM_PROMPT_EKI = ""


# ======================= ARAÇLAR =======================

def _kisalt(yazi, sinir=CIKTI_SINIRI):
    yazi = yazi or ""
    return yazi if len(yazi) <= sinir else yazi[:sinir] + f"\n... ({len(yazi) - sinir} karakter kesildi)"


def _yol(yol):
    return os.path.abspath(os.path.expanduser(yol))


def _calistir(argv, zaman=30, girdi=None):
    s = subprocess.run(argv, capture_output=True, text=True, timeout=zaman, input=girdi)
    return _kisalt((s.stdout or "") + (("\n[hata] " + s.stderr) if s.returncode and s.stderr else ""))


def _osascript(kod):
    return _calistir(["osascript", "-e", kod])


# Hiçbir koşulda çalıştırılmayacak komut kalıpları
YASAK = [
    r"\bsudo\b", r"\brm\s+-[a-z]*r", r"\bmkfs", r"\bdiskutil\s+(erase|partition|zero)", r"\bdd\s+if=",
    r":\(\)\s*\{", r"\bshutdown\b", r"\breboot\b", r"\bchmod\s+-R\s+777\s+/", r">\s*/dev/", r"\blaunchctl\b",
    r"\bcurl\b[^|]*\|\s*(ba|z)?sh", r"\bcsrutil\b", r"\bnvram\b", r"/System/", r"/Library/",
]
# Onay istemeden çalıştırılabilecek, sadece okuyan komutlar
SORMADAN = {"ls", "pwd", "date", "cal", "whoami", "df", "du", "uptime", "sw_vers", "which", "echo", "wc", "head", "tail", "cat", "find", "mdfind", "file", "stat", "pmset"}


def komut_tehlikeli_mi(komut):
    return any(re.search(k, komut) for k in YASAK)


def komut_sormadan_mi(komut):
    if any(c in komut for c in [";", "&&", "||", "|", ">", "<", "`", "$("]):
        return False
    try:
        ilk = shlex.split(komut)[0]
    except Exception:
        return False
    return ilk in SORMADAN


def uygulama_ac(ad):
    return _calistir(["open", "-a", ad]) or f"{ad} açıldı."


def web_ac(adres):
    if not re.match(r"^https?://", adres):
        adres = "https://" + adres
    return _calistir(["open", adres]) or f"{adres} açıldı."


def dosya_ac(yol):
    yol = _yol(yol)
    if not os.path.exists(yol):
        return f"Böyle bir dosya yok: {yol}"
    return _calistir(["open", yol]) or f"{yol} açıldı."


def dosya_ara(sorgu, klasor="~"):
    cikti = _calistir(["mdfind", "-onlyin", _yol(klasor), "-name", sorgu], zaman=20)
    satirlar = [s for s in cikti.splitlines() if s.strip()]
    return "\n".join(satirlar[:25]) or "Hiçbir şey bulunamadı."


def klasor_listele(yol="~"):
    yol = _yol(yol)
    if not os.path.isdir(yol):
        return f"Klasör yok: {yol}"
    ogeler = sorted(os.listdir(yol))[:200]
    return "\n".join(("[klasör] " if os.path.isdir(os.path.join(yol, o)) else "") + o for o in ogeler if not o.startswith(".")) or "(boş)"


def dosya_oku(yol):
    yol = _yol(yol)
    if not os.path.isfile(yol):
        return f"Dosya yok: {yol}"
    if yol.lower().endswith(".pdf"):
        # macOS'ta PDF metni: önce pdftotext (varsa), yoksa mdls ile içerik özeti
        if subprocess.run(["which", "pdftotext"], capture_output=True).returncode == 0:
            return _calistir(["pdftotext", "-l", "15", yol, "-"])
        return _calistir(["mdls", "-name", "kMDItemTextContent", "-raw", yol])
    if yol.lower().endswith((".docx", ".doc", ".rtf", ".pages")):
        return _calistir(["textutil", "-convert", "txt", "-stdout", yol])
    with open(yol, "r", encoding="utf-8", errors="replace") as f:
        return _kisalt(f.read())


def dosya_yaz(yol, icerik, ekle=False):
    yol = _yol(yol)
    if not yol.startswith(EV):
        return "Sadece kendi ev klasörünün içine yazabilirim."
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    with open(yol, "a" if ekle else "w", encoding="utf-8") as f:
        f.write(icerik)
    return f"Yazıldı: {yol}"


def tasi(kaynak, hedef):
    kaynak, hedef = _yol(kaynak), _yol(hedef)
    if not (kaynak.startswith(EV) and hedef.startswith(EV)):
        return "Sadece ev klasörünün içinde taşıma yapabilirim."
    os.makedirs(os.path.dirname(hedef) if not os.path.isdir(hedef) else hedef, exist_ok=True)
    os.rename(kaynak, os.path.join(hedef, os.path.basename(kaynak)) if os.path.isdir(hedef) else hedef)
    return f"Taşındı: {kaynak} -> {hedef}"


def cop_kutusuna_tasi(yol):
    yol = _yol(yol)
    if not yol.startswith(EV) or not os.path.exists(yol):
        return "Bu dosyayı bulamadım ya da ev klasörünün dışında."
    yol_as = yol.replace('"', '\\"')
    return _osascript(f'tell application "Finder" to delete POSIX file "{yol_as}"') or f"Çöp Kutusu'na taşındı: {yol}"


def komut_calistir(komut):
    if komut_tehlikeli_mi(komut):
        return "Bu komut güvenlik listemde yasak, çalıştırmadım."
    s = subprocess.run(komut, shell=True, capture_output=True, text=True, timeout=60, cwd=EV)
    return _kisalt(f"(çıkış kodu {s.returncode})\n{s.stdout}{s.stderr}")


def applescript(kod):
    if komut_tehlikeli_mi(kod) or re.search(r"do shell script", kod, re.I):
        return "Bu AppleScript güvenlik listemde yasak."
    return _osascript(kod) or "Tamam."


def ses_seviyesi(seviye):
    seviye = max(0, min(100, int(seviye)))
    _osascript(f"set volume output volume {seviye}")
    return f"Ses %{seviye}."


def bildirim(baslik, mesaj):
    b, m = baslik.replace('"', "'"), mesaj.replace('"', "'")
    _osascript(f'display notification "{m}" with title "{b}"')
    return "Bildirim gönderildi."


def muzik(islem):
    komutlar = {"cal": "play", "durdur": "pause", "sonraki": "next track", "onceki": "previous track"}
    if islem == "simdi":
        return _osascript('tell application "Music" to if player state is playing then return (name of current track) & " - " & (artist of current track)') or "Şu an bir şey çalmıyor."
    if islem not in komutlar:
        return "İşlem: cal, durdur, sonraki, onceki ya da simdi."
    _osascript(f'tell application "Music" to {komutlar[islem]}')
    if islem == "cal" and yuz:
        yuz.dans()
    return "Tamam."


def ekran_goruntusu():
    yol = os.path.join(EV, "Desktop", f"enki_ekran_{time.strftime('%Y%m%d_%H%M%S')}.png")
    _calistir(["screencapture", "-x", yol])
    if yuz and os.path.exists(yol):
        try:
            yuz.sun("Ekran görüntüsü", yuz.gorsel("ekran", yol, aciklama=os.path.basename(yol)))
        except Exception:
            pass
    return f"Kaydedildi ve ekranımda gösterildi: {yol}"


def ekranda_goster(baslik, kartlar):
    if not yuz:
        return "Yüz ekranı bağlı değil."
    ogeler = []
    for k in kartlar:
        t, i = k.get("tur"), k.get("id")
        if t == "metin":
            ogeler.append(yuz.metin(i, k.get("yazi", ""), etiket=k.get("etiket")))
        elif t == "liste":
            ogeler.append(yuz.liste(i, k.get("maddeler", []), etiket=k.get("etiket")))
        elif t == "kod":
            ogeler.append(yuz.kod(i, k.get("kod", ""), etiket=k.get("etiket")))
        elif t == "grafik":
            ogeler.append(yuz.grafik(i, k.get("etiketler", []), k.get("degerler", []), baslik=k.get("baslik"), tur=k.get("grafik_turu", "bar")))
        elif t == "gorsel":
            ogeler.append(yuz.gorsel(i, k.get("kaynak", ""), aciklama=k.get("aciklama")))
        elif t == "web":
            ogeler.append(yuz.web(i, k.get("adres", "")))
    yuz.sun(baslik, *ogeler)
    return f"{len(ogeler)} kart ekranda."


def isaret_et(id, sira=None):
    if yuz:
        yuz.goster(id, sira)
    return "Tamam."


def ekrani_kapat():
    if yuz:
        yuz.sunumu_kapat()
    return "Tamam."


# ---------- Ekranı görme ve fare / klavye ----------
GORUNTU_GENISLIK = 1280   # modele gönderilen ekran görüntüsünün genişliği


def _pyautogui():
    import pyautogui
    pyautogui.FAILSAFE = True    # acil durdurma: fareyi ekranın sol üst köşesine götür
    pyautogui.PAUSE = 0.05
    return pyautogui


def _olcek():
    """Ekran görüntüsü pikseli -> macOS ekran noktası çarpanı."""
    w, _ = _pyautogui().size()
    return w / GORUNTU_GENISLIK


def ekrana_bak():
    import base64
    import io
    pg = _pyautogui()
    resim = pg.screenshot()
    w, h = pg.size()
    resim = resim.resize((GORUNTU_GENISLIK, round(h * GORUNTU_GENISLIK / w)))
    tampon = io.BytesIO()
    resim.save(tampon, format="PNG", optimize=True)
    return {"_gorsel": base64.b64encode(tampon.getvalue()).decode(),
            "metin": f"Ekran görüntüsü {resim.width}x{resim.height}. Koordinatlar bu görüntüye göre."}


def _noktaya(x, y):
    k = _olcek()
    return x * k, y * k


def git(x, y):
    """Enki'yi ekranda bir noktanın yanına uçurur (bir şeyi göstermek için)."""
    if yuz:
        px, py = _noktaya(x, y)
        yuz.enki_git(px, py)
    return "Oradayım."


def tikla(x, y, cift=False, sag=False):
    pg = _pyautogui()
    px, py = _noktaya(x, y)
    if yuz:
        yuz.enki_git(px, py)           # önce Enki oraya uçar, ne yapacağını görürsün
        time.sleep(0.7)
    pg.moveTo(px, py, duration=0.35)
    if cift:
        pg.doubleClick()
    else:
        pg.click(button="right" if sag else "left")
    time.sleep(0.4)
    return f"Tıklandı ({x}, {y})."


def yaz(metin):
    """Türkçe karakterler bozulmasın diye panodan yapıştırır."""
    pg = _pyautogui()
    subprocess.run(["pbcopy"], input=metin, text=True)
    pg.hotkey("command", "v")
    time.sleep(0.2)
    return "Yazıldı."


TUSLAR = {"cmd": "command", "komut": "command", "ctrl": "ctrl", "alt": "option", "opt": "option", "option": "option",
          "shift": "shift", "enter": "enter", "return": "enter", "esc": "esc", "tab": "tab", "space": "space", "bosluk": "space",
          "sil": "backspace", "backspace": "backspace", "yukari": "up", "asagi": "down", "sol": "left", "sag": "right"}


def tus(kombinasyon):
    pg = _pyautogui()
    tuslar = [TUSLAR.get(t.strip().lower(), t.strip().lower()) for t in kombinasyon.split("+")]
    if tuslar[:2] == ["command", "q"] or "power" in tuslar:
        return "Uygulama kapatma ve güç tuşları için önce Nehir'e sor."
    pg.hotkey(*tuslar)
    time.sleep(0.25)
    return f"Basıldı: {kombinasyon}"


def kaydir(miktar):
    _pyautogui().scroll(int(miktar))
    return "Kaydırıldı."


def masaustu_ogeleri():
    """Masaüstündeki dosya ve klasörler ile ekrandaki yerleri (ekran görüntüsü koordinatında)."""
    ham = _osascript('''set c to {}
tell application "Finder"
    repeat with i in (items of desktop)
        set p to desktop position of i
        set end of c to (name of i) & "|" & (item 1 of p) & "|" & (item 2 of p)
    end repeat
end tell
set AppleScript's text item delimiters to linefeed
return c as text''')
    try:
        k = _olcek()
    except Exception:
        k = 1
    satirlar = []
    for s in ham.splitlines():
        parca = s.split("|")
        if len(parca) == 3:
            try:
                satirlar.append(f"{parca[0]}: ({round(float(parca[1]) / k)}, {round(float(parca[2]) / k)})")
            except ValueError:
                pass
    return "\n".join(satirlar) or ham or "Masaüstü boş."


def pencereler():
    """Açık pencereler: uygulama, başlık, konum ve boyut (ekran görüntüsü koordinatında)."""
    ham = _osascript('''set c to {}
tell application "System Events"
    repeat with pr in (processes whose visible is true)
        repeat with w in (windows of pr)
            try
                set {x, y} to position of w
                set {g, h} to size of w
                set end of c to (name of pr) & "|" & (name of w) & "|" & x & "|" & y & "|" & g & "|" & h
            end try
        end repeat
    end repeat
end tell
set AppleScript's text item delimiters to linefeed
return c as text''')
    try:
        k = _olcek()
    except Exception:
        k = 1
    satirlar = []
    for s in ham.splitlines():
        p = s.split("|")
        if len(p) == 6:
            try:
                x, y, g, h = (round(float(v) / k) for v in p[2:])
                satirlar.append(f"{p[0]} — {p[1] or '(adsız)'}: konum ({x}, {y}), boyut {g}x{h}")
            except ValueError:
                pass
    return "\n".join(satirlar) or ham or "Açık pencere yok."


FARE_ARACLARI = {"tikla", "yaz", "tus", "kaydir"}   # görev başına bir kez izin ister


# Araç adı -> (fonksiyon, onay gerekli mi, kullanıcıya gösterilecek kısa açıklama)
ARACLAR = {
    "uygulama_ac":       (uygulama_ac, False, "Uygulama açılıyor"),
    "web_ac":            (web_ac, False, "Site açılıyor"),
    "dosya_ac":          (dosya_ac, False, "Dosya açılıyor"),
    "dosya_ara":         (dosya_ara, False, "Dosya aranıyor"),
    "klasor_listele":    (klasor_listele, False, "Klasöre bakılıyor"),
    "dosya_oku":         (dosya_oku, False, "Dosya okunuyor"),
    "dosya_yaz":         (dosya_yaz, True, "Dosyaya yazılıyor"),
    "tasi":              (tasi, True, "Dosya taşınıyor"),
    "cop_kutusuna_tasi": (cop_kutusuna_tasi, True, "Çöp Kutusu'na taşınıyor"),
    "komut_calistir":    (komut_calistir, True, "Komut çalışıyor"),
    "applescript":       (applescript, True, "Uygulama kontrol ediliyor"),
    "ses_seviyesi":      (ses_seviyesi, False, "Ses ayarlanıyor"),
    "bildirim":          (bildirim, False, "Bildirim"),
    "muzik":             (muzik, False, "Müzik"),
    "ekran_goruntusu":   (ekran_goruntusu, False, "Ekran görüntüsü"),
    "ekranda_goster":    (ekranda_goster, False, "Ekranımda gösteriyorum"),
    "isaret_et":         (isaret_et, False, "Gösteriyorum"),
    "ekrani_kapat":      (ekrani_kapat, False, "Ekran kapanıyor"),
    "ekrana_bak":        (ekrana_bak, False, "Ekrana bakıyorum"),
    "git":               (git, False, "Gösteriyorum"),
    "tikla":             (tikla, False, "Tıklıyorum"),
    "yaz":               (yaz, False, "Yazıyorum"),
    "tus":               (tus, False, "Tuşa basıyorum"),
    "kaydir":            (kaydir, False, "Kaydırıyorum"),
    "masaustu_ogeleri":  (masaustu_ogeleri, False, "Masaüstüne bakıyorum"),
    "pencereler":        (pencereler, False, "Pencerelere bakıyorum"),
}

_s = lambda **p: {"type": "object", "properties": p, "required": [k for k, v in p.items() if not v.pop("_opsiyonel", False)]}
_str = lambda d, ops=False: {"type": "string", "description": d, **({"_opsiyonel": True} if ops else {})}

ARAC_TANIMLARI = [
    {"name": "uygulama_ac", "description": "Mac'te bir uygulamayı açar (ör. Safari, Spotify, Visual Studio Code, Notes).", "input_schema": _s(ad=_str("Uygulamanın adı"))},
    {"name": "web_ac", "description": "Varsayılan tarayıcıda bir web adresini açar.", "input_schema": _s(adres=_str("URL"))},
    {"name": "dosya_ac", "description": "Bir dosyayı varsayılan uygulamasıyla açar.", "input_schema": _s(yol=_str("Dosya yolu, ~ kullanılabilir"))},
    {"name": "dosya_ara", "description": "Spotlight ile adında sorgu geçen dosyaları bulur.", "input_schema": _s(sorgu=_str("Aranacak isim parçası"), klasor=_str("Aranacak klasör, varsayılan ~", True))},
    {"name": "klasor_listele", "description": "Bir klasördeki dosya ve klasörleri listeler.", "input_schema": _s(yol=_str("Klasör yolu, varsayılan ~", True))},
    {"name": "dosya_oku", "description": "Metin, kod, PDF ya da Word dosyasının içeriğini okur.", "input_schema": _s(yol=_str("Dosya yolu"))},
    {"name": "dosya_yaz", "description": "Ev klasörü içinde bir dosya oluşturur ya da üzerine yazar. Onay ister.", "input_schema": _s(yol=_str("Dosya yolu"), icerik=_str("Yazılacak içerik"), ekle={"type": "boolean", "description": "true ise sona ekler", "_opsiyonel": True})},
    {"name": "tasi", "description": "Dosya ya da klasörü taşır veya yeniden adlandırır. Onay ister.", "input_schema": _s(kaynak=_str("Kaynak yol"), hedef=_str("Hedef yol ya da klasör"))},
    {"name": "cop_kutusuna_tasi", "description": "Silmek yerine dosyayı Çöp Kutusu'na taşır. Onay ister.", "input_schema": _s(yol=_str("Dosya yolu"))},
    {"name": "komut_calistir", "description": "Terminal komutu çalıştırır (zsh, ev klasöründe). Sadece okuyan basit komutlar dışında onay ister.", "input_schema": _s(komut=_str("Komut"))},
    {"name": "applescript", "description": "Mac uygulamalarını AppleScript ile kontrol eder (Takvim, Hatırlatıcılar, Notlar, Finder pencereleri vb.). Onay ister.", "input_schema": _s(kod=_str("AppleScript kodu"))},
    {"name": "ses_seviyesi", "description": "Mac'in ses seviyesini 0-100 arası ayarlar.", "input_schema": _s(seviye={"type": "integer", "description": "0-100"})},
    {"name": "bildirim", "description": "Mac'te bildirim gösterir.", "input_schema": _s(baslik=_str("Başlık"), mesaj=_str("Mesaj"))},
    {"name": "muzik", "description": "Music uygulamasını kontrol eder. islem: cal, durdur, sonraki, onceki, simdi (çalanı söyler).", "input_schema": _s(islem=_str("cal | durdur | sonraki | onceki | simdi"))},
    {"name": "ekran_goruntusu", "description": "Ekran görüntüsü alır, Masaüstüne kaydeder ve Enki'nin ekranında gösterir.", "input_schema": _s()},
    {"name": "ekranda_goster", "description": "Enki'nin kendi ekranında kartlar açar; yüz kenara geçer. Kart türleri: metin(yazi), liste(maddeler), kod(kod), grafik(etiketler, degerler, baslik, grafik_turu bar|line), gorsel(kaynak: yol ya da URL, aciklama), web(adres). Her kartın benzersiz id'si ve isteğe bağlı etiketi olur.",
     "input_schema": {"type": "object", "properties": {"baslik": {"type": "string"}, "kartlar": {"type": "array", "items": {"type": "object"}}}, "required": ["baslik", "kartlar"]}},
    {"name": "isaret_et", "description": "Ekrandaki bir karta gidip ona bakar; sira verilirse listede maddeyi, grafikte çubuğu, kodda satırı vurgular (0'dan başlar).", "input_schema": _s(id=_str("Kart id"), sira={"type": "integer", "_opsiyonel": True})},
    {"name": "ekrani_kapat", "description": "Enki'nin ekranındaki kartları kapatır, yüz ortaya döner.", "input_schema": _s()},
    {"name": "ekrana_bak", "description": "Mac ekranının görüntüsünü alır. tikla/git/masaustu_ogeleri/pencereler koordinatları bu görüntüye göredir.", "input_schema": _s()},
    {"name": "git", "description": "Masaüstündeki Enki'yi ekranda bir noktanın yanına uçurur; bir klasörü ya da şeyi Nehir'e göstermek için.", "input_schema": _s(x={"type": "integer"}, y={"type": "integer"})},
    {"name": "tikla", "description": "Ekranda bir noktaya tıklar. Enki önce oraya uçar, imleç görünür şekilde gider. cift=true çift tık, sag=true sağ tık.", "input_schema": _s(x={"type": "integer"}, y={"type": "integer"}, cift={"type": "boolean", "_opsiyonel": True}, sag={"type": "boolean", "_opsiyonel": True})},
    {"name": "yaz", "description": "Odaktaki alana metin yazar (Türkçe karakterler dahil).", "input_schema": _s(metin=_str("Yazılacak metin"))},
    {"name": "tus", "description": "Tuş ya da kısayol basar: 'enter', 'esc', 'tab', 'cmd+space', 'cmd+t', 'cmd+shift+4'...", "input_schema": _s(kombinasyon=_str("Tuşlar, + ile"))},
    {"name": "kaydir", "description": "Fare tekerleği: pozitif yukarı, negatif aşağı (ör. -5).", "input_schema": _s(miktar={"type": "integer"})},
    {"name": "masaustu_ogeleri", "description": "Masaüstündeki dosya ve klasörlerin adlarını ve ekrandaki konumlarını verir.", "input_schema": _s()},
    {"name": "pencereler", "description": "Açık pencerelerin uygulama adı, başlığı, konumu ve boyutu.", "input_schema": _s()},
]


def terminalden_onay(soru):
    try:
        return input(f"\n[Enki onay istiyor] {soru}\n  Yapayım mı? (e/h): ").strip().lower() in ("e", "evet", "y", "yes")
    except EOFError:
        return False


def araci_calistir(ad, girdi, onay=terminalden_onay):
    if ad not in ARACLAR:
        return f"Bilinmeyen araç: {ad}"
    fonk, onay_gerekli, _ = ARACLAR[ad]
    if ad == "komut_calistir":
        if komut_tehlikeli_mi(girdi.get("komut", "")):
            return "Bu komut güvenlik listemde yasak, çalıştırmadım."
        onay_gerekli = not komut_sormadan_mi(girdi.get("komut", ""))
    if onay_gerekli:
        ozet = ", ".join(f"{k}={str(v)[:120]}" for k, v in girdi.items())
        if not onay(f"{ad}({ozet})"):
            return "Nehir bunu onaylamadı. Yapmadım."
    try:
        sonuc = fonk(**girdi)
        return sonuc if isinstance(sonuc, dict) else str(sonuc)
    except subprocess.TimeoutExpired:
        return "Zaman aşımı: iş çok uzun sürdü."
    except Exception as h:
        return f"Hata: {type(h).__name__}: {h}"


# ======================= GÖREV DÖNGÜSÜ =======================

_gecmis = []  # konuşma hafızası (bu oturum için)


def _yuz(durum, metin=None):
    if yuz:
        try:
            yuz.durum(durum, metin)
        except Exception:
            pass


def _gorselsiz(mesajlar):
    """Hafızada eski ekran görüntülerini tutma (ağır ve eskimiş olurlar)."""
    temiz = []
    for m in mesajlar:
        c = m["content"]
        if isinstance(c, list):
            yeni = []
            for p in c:
                if isinstance(p, dict) and p.get("type") == "tool_result" and isinstance(p.get("content"), list):
                    p = {**p, "content": " ".join(x.get("text", "[ekran görüntüsü]") if x.get("type") == "text" else "[ekran görüntüsü]" for x in p["content"])}
                yeni.append(p)
            c = yeni
        temiz.append({"role": m["role"], "content": c})
    # geçmiş bir araç sonucuyla başlamasın
    while temiz and temiz[0]["role"] != "user" or (temiz and isinstance(temiz[0]["content"], list) and any(isinstance(p, dict) and p.get("type") == "tool_result" for p in temiz[0]["content"])):
        temiz.pop(0)
    return temiz


def gorev(istek, konus=print, onay=terminalden_onay, hafiza=True):
    """Nehir'in isteğini alır, gerekirse bilgisayarda araç kullanır, sonunda Enki'nin cevabını döndürür."""
    import anthropic

    istemci = anthropic.Anthropic()
    mesajlar = (_gecmis if hafiza else []) + [{"role": "user", "content": istek}]
    sistem = KISILIK + "\n\n" + SISTEM_PROMPT_EKI
    _yuz("thinking")
    fare_izni = False

    for adim in range(EN_FAZLA_ADIM):
        try:
            yanit = istemci.messages.create(model=MODEL, max_tokens=1500, system=sistem, tools=ARAC_TANIMLARI, messages=mesajlar)
        except Exception as h:
            _yuz("glitch", "Beynime ulaşamıyorum. Bağlantıyı kontrol eder misiniz?")
            return f"[hata] {h}"

        mesajlar.append({"role": "assistant", "content": yanit.content})
        araclar = [b for b in yanit.content if b.type == "tool_use"]

        if not araclar:
            metin = " ".join(b.text for b in yanit.content if b.type == "text").strip()
            duygu, temiz = (yuz.ayikla(metin) if yuz else ("speaking", metin))
            _yuz(duygu, temiz)
            konus(temiz)
            _yuz("idle")
            if hafiza:
                _gecmis[:] = _gorselsiz(mesajlar[-30:])  # son 30 mesaj, ekran görüntüleri olmadan
            return temiz

        sonuclar = []
        for b in araclar:
            gorunen = ARACLAR.get(b.name, (None, None, b.name))[2]
            if b.name not in ("ekranda_goster", "isaret_et", "ekrani_kapat", "git"):
                _yuz("focus", gorunen)
            if b.name in FARE_ARACLARI and not fare_izni:
                fare_izni = onay("Bu görev için fare ve klavyeni kullanmama izin veriyor musun? (Durdurmak için fareyi ekranın sol üst köşesine götür.)")
                if not fare_izni:
                    sonuclar.append({"type": "tool_result", "tool_use_id": b.id, "content": "Nehir fare ve klavye iznini vermedi. Başka bir yol dene ya da ona söyle."})
                    continue
            sonuc = araci_calistir(b.name, dict(b.input), onay=onay)
            if isinstance(sonuc, dict) and "_gorsel" in sonuc:
                icerik = [{"type": "text", "text": sonuc.get("metin", "")},
                          {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": sonuc["_gorsel"]}}]
            else:
                icerik = str(sonuc)
            sonuclar.append({"type": "tool_result", "tool_use_id": b.id, "content": icerik})
        mesajlar.append({"role": "user", "content": sonuclar})
        _yuz("thinking")

    _yuz("confused", "Bu iş beklediğimden uzun sürdü, yarıda bıraktım.")
    return "Adım sınırına ulaşıldı."


if __name__ == "__main__":
    # Terminalden dene: python3 enki_eller.py
    if yuz:
        yuz.baslat()
        yuz.acilis_bitti("idle")
    print("Enki dinliyor. Çıkmak için boş satır.")
    while True:
        try:
            istek = input("\nSen: ").strip()
        except EOFError:
            break
        if not istek:
            break
        print("Enki:", gorev(istek, konus=lambda m: None))
