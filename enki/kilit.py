"""Kilit ekranı (macOS): python -m enki.kilit <token>

Enki'den ayrı süreç. Enki çökse ya da kapatılsa da bu kalır; Ctrl+C / kill (TERM, HUP, INT) işlemez.
  • Her ekranı kaplayan siyah pencere, ekran koruyucu seviyesinde (her şeyin üstünde)
  • Kiosk modu: Dock + menü çubuğu gizli, Cmd+Tab / Zorla Çık / Oturumu Kapat devre dışı
  • Event tap: tüm klavye + fare + trackpad olayları yutulur; imleç donar (fare imleçten ayrılır)
  • Açılış: Enki data/kilit.ac'ye token yazınca (yüzünü gördü) ya da ekranda Enki şifresi + Enter
İzin: Terminal'e Gizlilik → Erişilebilirlik izni gerekir (eller için zaten lazım).
İzin yoksa (event tap kurulamaz) → kısıtlı kilit: aynı siyah ekran + kiosk + Cmd+Q engeli, şifre kutusu;
imleç oynar ama tıklamalar ve tuşlar sadece kilit penceresine gider. macOS da izin penceresini açar.
Çıkış kodları: 0 = açıldı, 2 = paket yok (yedek: ekranı uyut).
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AC = ROOT / "data" / "kilit.ac"
PID = ROOT / "data" / "kilit.pid"


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} kilit: {msg}", flush=True)


def macos_kilidi() -> None:
    """Yedek: ekranı uyut. Sistem Ayarları → Kilit Ekranı → 'Şifre iste: hemen' ise Mac kilitlenir."""
    subprocess.run(["pmset", "displaysleepnow"], check=False)


def main() -> int:
    token = sys.argv[1] if len(sys.argv) > 1 else ""
    for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGTSTP):
        signal.signal(s, signal.SIG_IGN)
    try:
        import Quartz
        import AppKit
        from PyObjCTools import AppHelper
    except ImportError as e:
        log(f"PyObjC yok ({e}) → macOS kilidi. Kur: pip install pyobjc-framework-Quartz pyobjc-framework-Cocoa")
        macos_kilidi()
        return 2

    from .sifre import verify, wait_left

    class KilitPenceresi(AppKit.NSWindow):  # kenarlıksız pencere normalde klavye alamaz
        def canBecomeKeyWindow(self):
            return True

        def canBecomeMainWindow(self):
            return True

    class Delege(AppKit.NSObject):
        def applicationShouldTerminate_(self, sender):  # Cmd+Q / "Çık" → hayır
            return 0  # NSTerminateCancel

        def sifreGirildi_(self, sender):
            state["buf"] = str(sender.stringValue())
            sender.setStringValue_("")
            submit()

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
    delege = Delege.alloc().init()
    app.setDelegate_(delege)

    # ---------------------------------------------------------------- pencereler
    windows, dots_labels, msg_labels = [], [], []

    def label(frame, text, size, color, bold=False):
        t = AppKit.NSTextField.alloc().initWithFrame_(frame)
        t.setStringValue_(text)
        t.setBezeled_(False); t.setDrawsBackground_(False); t.setEditable_(False); t.setSelectable_(False)
        t.setAlignment_(AppKit.NSTextAlignmentCenter)
        t.setTextColor_(color)
        t.setFont_(AppKit.NSFont.boldSystemFontOfSize_(size) if bold else AppKit.NSFont.systemFontOfSize_(size))
        return t

    cyan = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.35, 0.85, 1.0, 1.0)
    grey = AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.6, 1.0)
    for screen in AppKit.NSScreen.screens():
        fr = screen.frame()
        w = KilitPenceresi.alloc().initWithContentRect_styleMask_backing_defer_(
            fr, AppKit.NSWindowStyleMaskBorderless, AppKit.NSBackingStoreBuffered, False)
        w.setLevel_(AppKit.NSScreenSaverWindowLevel)
        w.setBackgroundColor_(AppKit.NSColor.blackColor())
        w.setOpaque_(True)
        w.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                                 | AppKit.NSWindowCollectionBehaviorStationary
                                 | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary)
        W, H = fr.size.width, fr.size.height
        v = w.contentView()
        v.addSubview_(label(AppKit.NSMakeRect(0, H / 2 + 40, W, 60), "◉  ◉", 44, cyan, True))
        v.addSubview_(label(AppKit.NSMakeRect(0, H / 2 - 10, W, 34),
                            "Enki bu bilgisayarı koruyor.", 26, AppKit.NSColor.whiteColor(), True))
        m = label(AppKit.NSMakeRect(0, H / 2 - 50, W, 26), "Sahibi kameraya bakınca açılır.", 17, grey)
        d = label(AppKit.NSMakeRect(0, H / 2 - 100, W, 30), "", 24, cyan)
        v.addSubview_(m); v.addSubview_(d)
        msg_labels.append(m); dots_labels.append(d)
        w.setFrame_display_(fr, True)
        w.orderFrontRegardless()
        windows.append(w)

    try:  # kiosk: Dock/menü yok, uygulama değiştirme / zorla çık / oturum kapatma yok
        app.setPresentationOptions_(
            AppKit.NSApplicationPresentationHideDock | AppKit.NSApplicationPresentationHideMenuBar
            | AppKit.NSApplicationPresentationDisableProcessSwitching
            | AppKit.NSApplicationPresentationDisableForceQuit
            | AppKit.NSApplicationPresentationDisableSessionTermination
            | AppKit.NSApplicationPresentationDisableHideApplication
            | AppKit.NSApplicationPresentationDisableAppleMenu)
    except Exception as e:
        log(f"kiosk modu kurulamadı: {e}")
    app.activateIgnoringOtherApps_(True)

    # ---------------------------------------------------------------- olay yakalama
    state = {"buf": "", "tap": None, "done": False, "alan": None}

    def show(msg: str | None = None) -> None:
        for d in dots_labels:
            d.setStringValue_("•" * len(state["buf"]))
        if msg is not None:
            for m in msg_labels:
                m.setStringValue_(msg)

    def finish(why: str) -> None:
        if state["done"]:
            return
        state["done"] = True
        log(f"açıldı ({why})")
        if state["tap"] is not None:
            Quartz.CGEventTapEnable(state["tap"], False)
        Quartz.CGAssociateMouseAndMouseCursorPosition(True)
        Quartz.CGDisplayShowCursor(Quartz.CGMainDisplayID())
        app.setPresentationOptions_(AppKit.NSApplicationPresentationDefault)
        for w in windows:
            w.orderOut_(None)
        PID.unlink(missing_ok=True)
        AC.unlink(missing_ok=True)
        os._exit(0)

    def submit() -> None:
        pw, state["buf"] = state["buf"], ""
        if not pw:
            return
        if (wl := wait_left()) > 0:
            show(f"Çok fazla yanlış deneme. {wl:.0f} sn bekle.")
            return
        if verify(pw):
            finish("şifre")
        else:
            log("yanlış şifre denendi")
            show("Yanlış şifre.")

    def cb(proxy, etype, event, refcon):
        if etype in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            Quartz.CGEventTapEnable(state["tap"], True)  # macOS yavaş callback'te tap'i kapatır: geri aç
            return event
        if etype == Quartz.kCGEventKeyDown:
            kc = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
            if kc in (36, 76):          # Return / Enter
                submit()
            elif kc == 51:              # Delete
                state["buf"] = state["buf"][:-1]
            elif kc == 53:              # Esc
                state["buf"] = ""
            elif len(state["buf"]) < 64:
                n, s = Quartz.CGEventKeyboardGetUnicodeString(event, 8, None, None)
                if s and s[:n].isprintable():
                    state["buf"] += s[:n]
            show()
        return None  # yut: hiçbir uygulamaya gitmez

    tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                  Quartz.kCGEventTapOptionDefault,
                                  getattr(Quartz, "kCGEventMaskForAllEvents", 0xFFFFFFFFFFFFFFFF), cb, None)
    if tap is None:
        log("event tap kurulamadı (Erişilebilirlik izni yok) → kısıtlı kilit: siyah ekran + şifre kutusu")
        try:  # macOS'un "Erişilebilirlik izni ver" penceresini aç
            from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
            AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
        except Exception:
            pass
        w = windows[0]
        W = w.frame().size.width
        H = w.frame().size.height
        alan = AppKit.NSSecureTextField.alloc().initWithFrame_(AppKit.NSMakeRect(W / 2 - 150, H / 2 - 105, 300, 32))
        alan.setPlaceholderString_("Enki şifresi + Enter")
        alan.setAlignment_(AppKit.NSTextAlignmentCenter)
        alan.setFont_(AppKit.NSFont.systemFontOfSize_(18))
        alan.setTarget_(delege)
        alan.setAction_("sifreGirildi:")
        w.contentView().addSubview_(alan)
        dots_labels[0].setHidden_(True)
        state["alan"] = alan
        for m in msg_labels:
            m.setStringValue_("Sahibi kameraya bakınca açılır.  (Erişilebilirlik izni yok: kısıtlı mod)")
    else:
        state["tap"] = tap
        src = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), src, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        Quartz.CGAssociateMouseAndMouseCursorPosition(False)  # fare oynasa da imleç kıpırdamaz
        Quartz.CGDisplayHideCursor(Quartz.CGMainDisplayID())
    log("kilitli" + ("" if tap is not None else " (kısıtlı)"))

    # ---------------------------------------------------------------- Enki'den açılış + ön planda kalma
    def poll() -> None:
        try:
            if token and AC.exists() and AC.read_text().strip() == token:
                finish("yüz")
                return
        except OSError:
            pass
        app.activateIgnoringOtherApps_(True)
        for w in windows:
            w.orderFrontRegardless()
        if (alan := state["alan"]) is not None and not windows[0].isKeyWindow():
            windows[0].makeKeyAndOrderFront_(None)
            windows[0].makeFirstResponder_(alan)
        AppHelper.callLater(0.3, poll)

    AppHelper.callLater(0.3, poll)
    AppHelper.runEventLoop(installInterrupt=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
