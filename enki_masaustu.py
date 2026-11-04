"""
Masaüstü Enki: ekranında her zaman üstte duran, şeffaf, sürüklenebilir küçük Enki.

- Yüz ekranıyla (enki-yuz.html) aynı duyguları ve animasyonları kullanır.
- Beyin (yuz_koprusu) ona "şuraya git", "büyü", "küçül", "imlecin yanına gel" der.
- Enki bilgisayarda bir şeye tıklayacağı zaman önce oraya uçar; ne yaptığını görürsün.
- Boşta dururken arada ekranın altında hafifçe dolaşır; menü çubuğundaki simgeden gizleyip gösterebilirsin.

Kurulum:   pip3 install PySide6 websockets
Çalıştır:  önce beyin (yuz_koprusu.baslat() çağıran kodun), sonra:  python3 enki_masaustu.py
Önizleme:  python3 enki_masaustu.py --demo     (beyin gerekmez; 35 saniyelik bir gösteri oynar)
"""

import json
import os
import random
import sys
import threading
import time

from PySide6.QtCore import QEasingCurve, QObject, QPoint, QPropertyAnimation, QRect, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QIcon, QPainter, QPixmap
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QVBoxLayout, QWidget

ADRES = "ws://localhost:8765"
YUZ_DOSYASI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "enki-yuz.html")
MINI_G, MINI_Y = 190, 130          # küçük Enki penceresinin boyutu
KENAR = 24                          # ekran kenarından boşluk
DOLASMA_ARALIGI = (35, 90)          # boşta iken kaç saniyede bir yer değiştirsin


class Kopru(QObject):
    """Beyinden gelen mesajları dinler (yüz sayfasıyla aynı sunucu), Qt tarafına iletir."""
    komut = Signal(dict)

    def baslat(self):
        threading.Thread(target=self._dinle, daemon=True).start()

    def _dinle(self):
        from websockets.sync.client import connect
        while True:
            try:
                with connect(ADRES, open_timeout=3) as ws:
                    for ham in ws:
                        try:
                            m = json.loads(ham)
                        except Exception:
                            continue
                        if "enki_git" in m or "enki_mod" in m or "present" in m or "present_close" in m:
                            self.komut.emit(m)
            except Exception:
                time.sleep(3)


class MasaustuEnki(QWidget):
    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow, True)   # başka uygulamaya geçince kaybolmasın
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self.gorunum = QWebEngineView(self)
        self.gorunum.page().setBackgroundColor(Qt.transparent)
        ayar = self.gorunum.settings()
        ayar.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)   # ws://localhost için
        ayar.setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
        url = QUrl.fromLocalFile(YUZ_DOSYASI)
        url.setQuery("mini")
        self.gorunum.load(url)
        self.gorunum.loadFinished.connect(self._yuklendi)
        duzen = QVBoxLayout(self)
        duzen.setContentsMargins(0, 0, 0, 0)
        duzen.addWidget(self.gorunum)

        self.buyuk = False
        self.son_hareket = time.time()
        self._surukle = None
        self.anim = QPropertyAnimation(self, b"geometry")
        self.anim.setEasingCurve(QEasingCurve.InOutCubic)

        ekran = (QApplication.primaryScreen()).availableGeometry()
        self.setGeometry(ekran.right() - MINI_G - KENAR, ekran.bottom() - MINI_Y - KENAR, MINI_G, MINI_Y)

        self.dolas = QTimer(self)
        self.dolas.timeout.connect(self._bosta_dolas)
        self.dolas.start(random.randint(*DOLASMA_ARALIGI) * 1000)

    # ---------- yardımcılar ----------
    def _ekran(self):
        return QApplication.screenAt(self.geometry().center() if self.isVisible() else QCursor.pos()) or QApplication.primaryScreen()

    def _alan(self):
        return self._ekran().availableGeometry()

    def _git_geo(self, hedef: QRect, sure=650):
        self.anim.stop()
        self.anim.setDuration(sure)
        self.anim.setStartValue(self.geometry())
        self.anim.setEndValue(hedef)
        self.anim.start()

    def _sinirla(self, x, y, g=MINI_G, y_=MINI_Y):
        a = self._alan()
        return max(a.left() + 4, min(a.right() - g - 4, x)), max(a.top() + 4, min(a.bottom() - y_ - 4, y))

    # ---------- komutlar ----------
    def git(self, x, y, sure=650):
        """Ekrandaki (x, y) noktasının hemen sağ üstüne uçar; tıklanacak yeri kapatmaz."""
        if self.buyuk:
            return
        hx, hy = self._sinirla(int(x) + 26, int(y) - MINI_Y - 18)
        self._git_geo(QRect(hx, hy, MINI_G, MINI_Y), sure)
        self.son_hareket = time.time()

    def imlece_gel(self):
        p = QCursor.pos()
        self.git(p.x(), p.y())

    def koseye_don(self):
        a = self._alan()
        self._git_geo(QRect(a.right() - MINI_G - KENAR, a.bottom() - MINI_Y - KENAR, MINI_G, MINI_Y), 900)

    def buyu(self):
        if self.buyuk:
            return
        self.buyuk = True
        self._kucuk_geo = self.geometry()
        self._git_geo(self._alan(), 500)

    def kucul(self):
        if not self.buyuk:
            return
        self.buyuk = False
        self._git_geo(getattr(self, "_kucuk_geo", QRect(0, 0, MINI_G, MINI_Y)), 500)

    def komut_al(self, m):
        if "enki_git" in m:
            g = m["enki_git"]
            self.git(g.get("x", 0), g.get("y", 0), g.get("sure", 650))
        mod = m.get("enki_mod")
        if m.get("present"):
            mod = "buyu"
        if m.get("present_close"):
            mod = "kucul"
        if mod == "buyu":
            self.buyu()
        elif mod == "kucul":
            self.kucul()
        elif mod == "imlec":
            self.imlece_gel()
        elif mod == "kose":
            self.koseye_don()
        elif mod == "gizle":
            self.hide()
        elif mod == "goster":
            self.show()

    def _bosta_dolas(self):
        """Boştayken arada bir ekranın alt kenarında biraz yer değiştirir; canlı ama dikkat dağıtmayan bir hareket."""
        self.dolas.start(random.randint(*DOLASMA_ARALIGI) * 1000)
        if self.buyuk or self._surukle or time.time() - self.son_hareket < 20:
            return
        a = self._alan()
        g = self.geometry()
        dx = random.choice([-1, 1]) * random.randint(60, 220)
        x, y = self._sinirla(g.x() + dx, a.bottom() - MINI_Y - KENAR)
        self._git_geo(QRect(x, y, MINI_G, MINI_Y), 2200)

    # ---------- sürükleme ----------
    def _yuklendi(self, _):
        hedef = self.gorunum.focusProxy()
        if hedef:
            hedef.installEventFilter(self)

    def eventFilter(self, nesne, olay):
        t = olay.type()
        if t == olay.Type.MouseButtonPress and olay.button() == Qt.LeftButton and not self.buyuk:
            self._surukle = olay.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.anim.stop()
            return True
        if t == olay.Type.MouseMove and self._surukle is not None:
            self.move(olay.globalPosition().toPoint() - self._surukle)
            return True
        if t == olay.Type.MouseButtonRelease and self._surukle is not None:
            self._surukle = None
            self.son_hareket = time.time()
            return True
        if t == olay.Type.MouseButtonDblClick and not self.buyuk:
            self.koseye_don()
            return True
        return super().eventFilter(nesne, olay)


def simge():
    """Menü çubuğu için iki gözlü küçük simge."""
    px = QPixmap(44, 44)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#9FE6FF"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(8, 12, 10, 20, 5, 5)
    p.drawRoundedRect(26, 12, 10, 20, 5, 5)
    p.end()
    return QIcon(px)


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    enki = MasaustuEnki()
    enki.show()

    kopru = Kopru()
    kopru.komut.connect(enki.komut_al)
    kopru.baslat()

    tepsi = QSystemTrayIcon(simge(), app)
    menu = QMenu()
    for ad, f in [("Göster", enki.show), ("Gizle", enki.hide), ("Köşeye dön", enki.koseye_don), ("İmlecin yanına gel", enki.imlece_gel)]:
        a = QAction(ad, menu)
        a.triggered.connect(f)
        menu.addAction(a)
    menu.addSeparator()
    cik = QAction("Enki'yi kapat", menu)
    cik.triggered.connect(app.quit)
    menu.addAction(cik)
    tepsi.setContextMenu(menu)
    tepsi.setToolTip("Enki")
    tepsi.show()

    if "--demo" in sys.argv:
        # Beyin olmadan önizleme: Enki sırayla imlece gelir, ekranda dolaşır, büyür, küçülür ve köşesine döner
        def js(kod):
            enki.gorunum.page().runJavaScript(kod)
        a = enki._alan()
        adimlar = [
            (3500, lambda: (enki.imlece_gel(), js("enki.set('owner')"))),
            (6500, lambda: (enki.git(a.left() + a.width() * 0.25, a.top() + a.height() * 0.3), js("enki.set('focus')"))),
            (9500, lambda: (enki.git(a.left() + a.width() * 0.7, a.top() + a.height() * 0.25), js("enki.set('smug')"))),
            (12500, lambda: (enki.buyu(), js("document.documentElement.classList.add('expanded'); enki.presentDemo()"))),
            (32000, lambda: (enki.kucul(), js("document.documentElement.classList.remove('expanded'); enki.set('blush')"))),
            (35000, lambda: (enki.koseye_don(), js("enki.set('idle')"))),
        ]
        for ms, f in adimlar:
            QTimer.singleShot(ms, f)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
