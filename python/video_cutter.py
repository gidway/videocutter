#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Video Cutter – Qt + VLC (single-window) + PNG placeholder + WebView banner (LEFT)
+ Persistent Settings (NVIDIA hw decode, H.265 export, remember window geometry)

Layout:
- LEFT: Web banner (http://gidway.net/banner) – pełna wysokość, stała szerokość
- RIGHT: Video (osadzone) + Slider (full width) + Controls + Checkboxes

Funkcje:
- Placeholder PNG, gdy brak/błąd wideo
- IN/OUT, szybki eksport FFmpeg:
    * -c copy (domyślnie)
    * H.265 (libx265 lub hevc_nvenc, gdy zaznaczone)
    * opcjonalny decode przez -hwaccel cuda
- Pasek postępu przy eksporcie, auto-zamykanie
- Skróty: Space, I, O, E, Ctrl+O, Ctrl+Q; ←/→ klatka; Shift+←/→ mikroprzesunięcie; Ctrl+C zamyka
- Ustawienia (QSettings): NVIDIA decode, H.265 export, remember geometry
- Klik w obszar filmu = Play/Pause
"""

import os
import re
import sys
import signal
import mimetypes
from pathlib import Path

# Wycisz libva (logi); HW decode kontrolujemy w FFmpeg
os.environ.setdefault("LIBVA_MESSAGING_LEVEL", "0")
os.environ.setdefault("LIBVA_DRIVER_NAME", "dummy")
# wyłącz wybrane kategorie logowania Qt/KF5
os.environ.setdefault("QT_LOGGING_RULES", "kf.kio.widgets.debug=false;kf.kio.core.debug=false")

# Wayland → XWayland: PRZED QApplication
if sys.platform.startswith("linux") and os.getenv("WAYLAND_DISPLAY") and not os.getenv("QT_QPA_PLATFORM"):
    os.environ["QT_QPA_PLATFORM"] = "xcb"

# Qt binding + (opcjonalnie) WebEngine
try:
    # --- PySide6 ---
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import Qt, QUrl
    from PySide6.QtGui import QKeySequence, QDesktopServices
    from PySide6.QtGui import QShortcut as QShortcutCls
    from PySide6.QtCore import QSettings
    QAction = QtGui.QAction
    PYSIDE_MAJOR = 6
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView
        WEBENGINE_OK = True
    except Exception:
        WEBENGINE_OK = False
except ModuleNotFoundError:
    # --- PySide2 ---
    from PySide2 import QtCore, QtGui, QtWidgets
    from PySide2.QtCore import Qt, QUrl
    from PySide2.QtGui import QKeySequence, QDesktopServices
    from PySide2.QtWidgets import QShortcut as QShortcutCls, QAction
    from PySide2.QtCore import QSettings
    PYSIDE_MAJOR = 2
    try:
        from PySide2.QtWebEngineWidgets import QWebEngineView
        WEBENGINE_OK = True
    except Exception:
        WEBENGINE_OK = False

# VLC
import vlc

# Konfiguracja UI
BANNER_WIDTH = 180  # szerokość lewej kolumny z banerem

# Placeholder PNG (możesz nadpisać ścieżką w env VIDCUT_PLACEHOLDER)
PLACEHOLDER_PNG = os.getenv("VIDCUT_PLACEHOLDER", str(Path(__file__).with_name("logo.png")))

# ----------------------------- Helpers -----------------------------
def ms_to_hhmmss_ms(ms: int | None) -> str:
    if ms is None or ms < 0:
        ms = 0
    secs, msec = divmod(int(ms), 1000)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.{msec:03d}"


class MarkingSlider(QtWidgets.QSlider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setOrientation(Qt.Horizontal)
        self.in_ms: int | None = None
        self.out_ms: int | None = None
        self.duration_ms: int = 0
        self.setEnabled(False)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            x = event.position().x() if hasattr(event, "position") else event.x()
            new_val = self.minimum() + (self.maximum() - self.minimum()) * x / max(1, self.width())
            self.setValue(int(new_val))
            event.accept()
        super().mousePressEvent(event)

    def set_duration(self, ms: int):
        self.duration_ms = max(0, int(ms))
        self.setRange(0, self.duration_ms if self.duration_ms > 0 else 1000)
        self.setEnabled(self.duration_ms > 0)

    def paintEvent(self, ev: QtGui.QPaintEvent):
        super().paintEvent(ev)
        if self.duration_ms <= 0:
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        opt = QtWidgets.QStyleOptionSlider(); self.initStyleOption(opt)
        groove = self.style().subControlRect(QtWidgets.QStyle.CC_Slider, opt, QtWidgets.QStyle.SC_SliderGroove, self)
        top = groove.top() + groove.height() // 4
        h = groove.height() // 2

        def px(val: int) -> int:
            handle = self.style().subControlRect(QtWidgets.QStyle.CC_Slider, opt, QtWidgets.QStyle.SC_SliderHandle, self)
            avail = groove.width() - handle.width()
            if avail <= 0:
                return groove.left()
            ratio = (val - self.minimum()) / max(1, (self.maximum()-self.minimum()))
            return int(groove.left() + handle.width()/2 + ratio * avail)

        if self.in_ms is not None and self.out_ms is not None and self.out_ms > self.in_ms:
            x1, x2 = px(self.in_ms), px(self.out_ms)
            p.fillRect(QtCore.QRect(min(x1,x2), top, abs(x2-x1), h), QtGui.QColor(120,200,140,90))
        pen_in = QtGui.QPen(QtGui.QColor(0,200,0), 2)
        pen_out = QtGui.QPen(QtGui.QColor(220,50,50), 2)
        if self.in_ms is not None:
            x = px(self.in_ms); p.setPen(pen_in); p.drawLine(x, top-3, x, top+h+6)
        if self.out_ms is not None:
            x = px(self.out_ms); p.setPen(pen_out); p.drawLine(x, top-3, x, top+h+6)
        p.end()


class BannerWidget(QtWidgets.QFrame):
    """
    Kolumna banera po lewej: jeśli WebEngine jest dostępny → QWebEngineView (render strony).
    Jeśli nie → QLabel (opcjonalnie obraz z URL/lokalnej ścieżki), klik/Link otwiera stronę w przeglądarce.
    """
    def __init__(self, url: str = "http://gidway.net/banner",
                 image_url: str | None = None, parent=None):
        super().__init__(parent)
        self._url = url
        self._image_url = image_url or os.getenv("VIDCUT_BANNER_IMG", "")
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setStyleSheet("background:#000; border-radius:8px;")

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        if WEBENGINE_OK:
            self.view = QWebEngineView(self)
            self.view.setUrl(QUrl(self._url))
            self.view.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            self.view.setContextMenuPolicy(QtCore.Qt.NoContextMenu)
            lay.addWidget(self.view)
        else:
            self.view = None
            self.label = QtWidgets.QLabel(self)
            self.label.setAlignment(Qt.AlignCenter)
            self.label.setStyleSheet("color:#ccc; background:#000;")
            self.label.setText(
                f'<div style="font:14px sans-serif; text-align:center; padding:12px;">'
                f'Qt WebEngine not available.<br>'
                f'Open: <a href="{self._url}">{self._url}</a></div>'
            )
            self.label.setOpenExternalLinks(True)
            self.label.setScaledContents(True)
            self.label.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            lay.addWidget(self.label)
            if self._image_url:
                self._load_image(self._image_url)

    def _load_image(self, src: str):
        if Path(src).exists():
            pm = QtGui.QPixmap(src)
            if not pm.isNull():
                self.label.setPixmap(pm)
            return
        if src.startswith(("http://", "https://")):
            try:
                import urllib.request
                with urllib.request.urlopen(src, timeout=5) as resp:
                    data = resp.read()
                pm = QtGui.QPixmap()
                if pm.loadFromData(data):
                    self.label.setPixmap(pm)
            except Exception:
                pass

    def mousePressEvent(self, ev: QtGui.QMouseEvent):
        if self.view is None and self._url:
            QDesktopServices.openUrl(QUrl(self._url))
        super().mousePressEvent(ev)


class ClickableFrame(QtWidgets.QFrame):
    clicked = QtCore.Signal()
    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# ----------------------------- Main Application -----------------------------
class VideoCutter(QtWidgets.QMainWindow):
    def __init__(self, initial_path: str | None = None):
        super().__init__()
        self.setWindowTitle("VideoCutter :: gidway.net")

        # --- SETTINGS ---
        self.settings = QSettings("gidway", "VideoCutter")
        self._remember_geometry = self.settings.value("remember_geometry", True, type=bool)

        # rozmiar/start
        if self._remember_geometry:
            geo = self.settings.value("geometry")
            if geo:
                try:
                    self.restoreGeometry(geo if isinstance(geo, (bytes, bytearray)) else bytes(geo))
                except Exception:
                    self.resize(1200, 800)
            else:
                self.resize(1200, 800)
        else:
            self.resize(1200, 800)

        # VLC core (stabilne wyjście X11, HW decode off by default)
        self.vlc_instance = vlc.Instance([
            "--quiet",
            "--no-video-title-show",
            "--avcodec-hw=none",
            "--vout=xcb_x11",
        ])
        self.mplayer = self.vlc_instance.media_player_new()

        # Centralny widget i główny układ H (baner lewa, reszta prawa)
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        hbox = QtWidgets.QHBoxLayout(central)
        hbox.setContentsMargins(10, 8, 10, 8)
        hbox.setSpacing(8)

        # --- LEFT: Banner column ---
        self.banner = BannerWidget(url="http://gidway.net/banner")
        self.banner.setMinimumWidth(BANNER_WIDTH)
        self.banner.setMaximumWidth(BANNER_WIDTH)
        hbox.addWidget(self.banner, 0)

        # --- RIGHT: Video + controls ---
        right = QtWidgets.QWidget()
        hbox_right = QtWidgets.QVBoxLayout(right)
        hbox_right.setContentsMargins(0, 0, 0, 0)
        hbox_right.setSpacing(8)
        hbox.addWidget(right, 1)

        # Stos: placeholder + video
        self.video_stack = QtWidgets.QStackedWidget()
        hbox_right.addWidget(self.video_stack, 1)

        self.placeholder = QtWidgets.QLabel()
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.placeholder.setStyleSheet("background:#111; border-radius:8px;")
        self.placeholder.setScaledContents(True)
        if os.path.isfile(PLACEHOLDER_PNG):
            pm = QtGui.QPixmap(PLACEHOLDER_PNG)
            self.placeholder.setPixmap(pm if not pm.isNull() else QtGui.QPixmap())
            if pm.isNull():
                self.placeholder.setText("No video loaded")
        else:
            self.placeholder.setText("No video loaded")

        self.video_frame = ClickableFrame()
        self.video_frame.clicked.connect(self.toggle)
        self.video_frame.setStyleSheet("background:#000; border-radius:8px;")
        self.video_frame.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.video_frame.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.video_frame.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

        self.video_stack.addWidget(self.placeholder)  # 0
        self.video_stack.addWidget(self.video_frame)  # 1
        self.video_stack.setCurrentIndex(0)

        # SLIDER – na całą szerokość (w prawej kolumnie, nad przyciskami)
        self.slider = MarkingSlider()
        hbox_right.addWidget(self.slider)
        self.slider.sliderPressed.connect(lambda: setattr(self, "_scrub", True))
        self.slider.sliderReleased.connect(self._slider_released)
        self.slider.sliderMoved.connect(lambda v: self._update_time_label(v, self._duration_ms))

        # PRZYCISKI
        ctr = QtWidgets.QHBoxLayout()
        hbox_right.addLayout(ctr)

        self.btn_play = QtWidgets.QPushButton("▶/⏸"); self.btn_play.clicked.connect(self.toggle); ctr.addWidget(self.btn_play)
        self.lbl_time = QtWidgets.QLabel("00:00:00.000 / 00:00:00.000"); self.lbl_time.setMinimumWidth(220); ctr.addWidget(self.lbl_time)

        self.btn_set_in = QtWidgets.QPushButton("IN (I)"); self.btn_set_in.clicked.connect(self.set_in)
        self.btn_set_out = QtWidgets.QPushButton("OUT (O)"); self.btn_set_out.clicked.connect(self.set_out)
        self.btn_clear = QtWidgets.QPushButton("Clear"); self.btn_clear.clicked.connect(self.clear_marks)
        for b in (self.btn_set_in, self.btn_set_out, self.btn_clear): ctr.addWidget(b)

        self.btn_export = QtWidgets.QPushButton("Export (E)"); self.btn_export.clicked.connect(self.export_clip); self.btn_export.setEnabled(False); ctr.addWidget(self.btn_export)
        self.btn_close = QtWidgets.QPushButton("Close"); self.btn_close.clicked.connect(self.request_close); ctr.addWidget(self.btn_close)

        # OPCJE / CHECKBOXY
        opts = QtWidgets.QHBoxLayout()
        hbox_right.addLayout(opts)

        self.chk_nvdec = QtWidgets.QCheckBox("Use NVIDIA decode (FFmpeg -hwaccel cuda)")
        self.chk_nvdec.setChecked(self.settings.value("use_nvdec", False, type=bool))
        opts.addWidget(self.chk_nvdec)

        self.chk_h265 = QtWidgets.QCheckBox("Export as H.265")
        self.chk_h265.setChecked(self.settings.value("use_h265", False, type=bool))
        opts.addWidget(self.chk_h265)

        self.chk_remember_geometry = QtWidgets.QCheckBox("Remember window size/position")
        self.chk_remember_geometry.setChecked(self._remember_geometry)
        opts.addWidget(self.chk_remember_geometry)

        opts.addStretch(1)

        # Skróty
        QShortcutCls(QKeySequence("Space"), self, self.toggle)
        QShortcutCls(QKeySequence("I"), self, self.set_in)
        QShortcutCls(QKeySequence("O"), self, self.set_out)
        QShortcutCls(QKeySequence("E"), self, self.export_clip)
        QShortcutCls(QKeySequence("Ctrl+O"), self, self.open_file_dialog)
        QShortcutCls(QKeySequence("Ctrl+Q"), self, self.request_close)
        QShortcutCls(QKeySequence(Qt.Key_Right), self, self.step_frame_forward)
        QShortcutCls(QKeySequence(Qt.Key_Left), self, self.step_frame_backward)
        QShortcutCls(QKeySequence("Shift+Right"), self, lambda: self.nudge_ms(+200))
        QShortcutCls(QKeySequence("Shift+Left"), self, lambda: self.nudge_ms(-200))

        # Menu
        bar = self.menuBar()
        filem = bar.addMenu("&File")
        act_open = QAction("Open… (Ctrl+O)", self); act_open.triggered.connect(self.open_file_dialog); filem.addAction(act_open)
        act_quit = QAction("Quit", self); act_quit.triggered.connect(self.close); filem.addAction(act_quit)
        helpm = bar.addMenu("&Help")
        about = QAction("About", self); about.triggered.connect(self._show_about); helpm.addAction(about)

        # Timer & state
        self.timer = QtCore.QTimer(self); self.timer.timeout.connect(self._poll_position); self.timer.start(100)
        self._scrub = False
        self._duration_ms: int = 0
        self._loaded_path: Path | None = None
        self._video_bound = False
        self._export_proc = None
        self._export_prog = None
        self._confirm_on_close = True
        self._frame_ms = 40
        self._have_next_frame = hasattr(self.mplayer, "next_frame")

        if initial_path:
            self.open_path(initial_path)

        # Status diagnostyczny
        self.statusBar().showMessage(f"WebEngine: {'OK' if WEBENGINE_OK else 'MISSING'} (PySide{PYSIDE_MAJOR})", 3000)

    # --- about ---
    def _show_about(self):
        QtWidgets.QMessageBox.information(
            self, "About",
            f"<h1>Gidway</h1>AppName: <b>VideoCutter</b><br />"
            f"AppVersion: PySide{PYSIDE_MAJOR} + VLC + FFmpeg<br />"
            "WebPage: <a href=\"https://gidway.net/?ref=videocutter\">www.gidway.net</a>"
            "<br /><br />Hotkeys: Space (Play/Pause), I (IN), O (OUT), E (Export), ←/→ frame"
        )

    def request_close(self):
        self.close()

    # ---- File handling ----
    def open_file_dialog(self):
        dlg = QtWidgets.QFileDialog(self, "Open Video", str(Path.home()))
        dlg.setFileMode(QtWidgets.QFileDialog.ExistingFile)
        dlg.setNameFilters([
            "Video files (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.mpg *.mpeg *.ts *.m2ts *.wmv)",
            "All files (*)",
        ])
        if dlg.exec():
            path = dlg.selectedFiles()[0]
            self.open_path(path)

    def _is_video_file(self, path: str) -> bool:
        ext_ok = {".mp4",".mkv",".mov",".avi",".webm",".m4v",".mpg",".mpeg",".ts",".m2ts",".wmv"}
        if Path(path).suffix.lower() in ext_ok:
            return True
        mime, _ = mimetypes.guess_type(path)
        return bool(mime and mime.startswith("video/"))

    def _show_placeholder(self, message: str | None = None):
        if message and (self.placeholder.pixmap() is None):
            self.placeholder.setText(message)
        self.video_stack.setCurrentIndex(0)

    def _show_video(self):
        self.video_stack.setCurrentIndex(1)

    def open_path(self, path: str):
        p = Path(path)
        if not p.exists():
            self._show_error("File not found.")
            return
        if not self._is_video_file(str(p)):
            self._show_error("This is not a video file.")
            return

        media = self.vlc_instance.media_new(str(p))
        if not media:
            self._show_error("Could not create media.")
            return
        media.add_option(":avcodec-hw=none")
        media.add_option(":vout=xcb_x11")
        self.mplayer.set_media(media)

        if not getattr(self, "_video_bound", False):
            try:
                wid = int(self.video_frame.winId())
                if sys.platform.startswith("linux"):
                    self.mplayer.set_xwindow(wid)
                elif sys.platform.startswith("win"):
                    self.mplayer.set_hwnd(wid)
                else:
                    self.mplayer.set_nsobject(wid)
                self._video_bound = True
            except Exception:
                self._show_error("Cannot bind video surface.")
                return

        if self.mplayer.play() == -1:
            self._show_error("Playback failed.")
            return

        self._loaded_path = p
        self._show_video()
        self._detect_fps(str(self._loaded_path))
        QtCore.QTimer.singleShot(200, self.mplayer.pause)
        self.statusBar().showMessage(f"Loaded: {p.name}")
        self.slider.setEnabled(True)
        self.btn_export.setEnabled(True)
        self.clear_marks()

    # ---- Playback ----
    def toggle(self):
        if self.mplayer.is_playing():
            self.mplayer.pause()
        else:
            self.mplayer.play()

    def _slider_released(self):
        self._scrub = False
        self.mplayer.set_time(int(self.slider.value()))

    def _poll_position(self):
        if self._loaded_path is None:
            return
        try:
            state = self.mplayer.get_state()
            if state in (vlc.State.Error,):
                self._show_error("Error while playing video.")
                return
        except Exception:
            self._show_error("Player error.")
            return

        dur = self.mplayer.get_length()
        if dur and dur > 0 and dur != self._duration_ms:
            self._duration_ms = int(dur)
            self.slider.set_duration(self._duration_ms)
        pos_ms = self.mplayer.get_time() or 0
        if not self._scrub:
            self.slider.setValue(int(pos_ms))
        self._update_time_label(int(pos_ms), self._duration_ms)

    def _update_time_label(self, cur_ms: int, dur_ms: int):
        self.lbl_time.setText(f"{ms_to_hhmmss_ms(cur_ms)} / {ms_to_hhmmss_ms(max(0, dur_ms))}")

    # ---- Frame stepping / nudging ----
    def _detect_fps(self, path: str):
        """Ustal FPS przez ffprobe; jeśli się nie uda, zostaw domyślne ~25fps (40ms)."""
        try:
            proc = QtCore.QProcess(self)
            args = ["-v","0","-of","csv=p=0","-select_streams","v:0","-show_entries","stream=r_frame_rate", path]
            proc.start("ffprobe", args)
            if not proc.waitForFinished(1500):
                proc.kill(); return
            out = bytes(proc.readAllStandardOutput()).decode("utf-8","ignore").strip()
            if not out:
                return
            if "/" in out:
                num, den = out.split("/", 1)
                num = float(num.strip()); den = float((den or "1").strip() or "1")
                fps = num/den if den else 0.0
            else:
                fps = float(out)
            if 0.1 < fps < 1000:
                self._frame_ms = max(1, int(round(1000.0 / fps)))
        except Exception:
            pass

    def _force_redraw(self, hold_ms: int | None = None):
        """Wymuś odrysowanie nowej klatki: krótkie play → pause."""
        try:
            self.mplayer.play()
            delay = self._frame_ms if hold_ms is None else int(max(1, hold_ms))
            QtCore.QTimer.singleShot(min(40, delay), self.mplayer.pause)
        except Exception:
            pass

    def nudge_ms(self, delta_ms: int, force_redraw: bool = True):
        """Przesuń odtwarzanie o delta_ms (ms) i zaktualizuj UI."""
        try:
            cur = int(self.mplayer.get_time() or 0)
            dur = int(self.mplayer.get_length() or 0)
            new_t = cur + int(delta_ms)
            if dur > 0:
                new_t = max(0, min(dur - 1, new_t))
            else:
                new_t = max(0, new_t)
            try: self.mplayer.pause()
            except Exception: pass
            self.mplayer.set_time(new_t)
            if force_redraw:
                self._force_redraw(self._frame_ms // 2)
            if not self._scrub:
                self.slider.setValue(new_t)
            self._update_time_label(new_t, self._duration_ms)
        except Exception:
            pass

    def step_frame_forward(self):
        self.nudge_ms(+self._frame_ms, force_redraw=True)

    def step_frame_backward(self):
        self.nudge_ms(-self._frame_ms, force_redraw=True)

    # ---- Marking ----
    def current_ms(self) -> int:
        t = self.mplayer.get_time()
        return int(t if t and t > 0 else 0)

    def set_in(self):
        t = self.current_ms()
        if self.slider.out_ms is not None and t >= self.slider.out_ms:
            self.slider.out_ms = None
        self.slider.in_ms = t
        self.slider.update()
        self.statusBar().showMessage(f"IN = {ms_to_hhmmss_ms(t)}", 2000)

    def set_out(self):
        t = self.current_ms()
        if self.slider.in_ms is None:
            self.slider.in_ms = 0
        if t <= (self.slider.in_ms or 0):
            t = (self.slider.in_ms or 0) + 1
        self.slider.out_ms = t
        self.slider.update()
        self.statusBar().showMessage(f"OUT = {ms_to_hhmmss_ms(t)}", 2000)

    def clear_marks(self):
        self.slider.in_ms = None
        self.slider.out_ms = None
        self.slider.update()

    def _show_error(self, msg: str):
        self.statusBar().showMessage(msg, 5000)
        try:
            try: self.mplayer.pause()
            except Exception: pass
            try: self.mplayer.stop()
            except Exception: pass
            try: self.mplayer.set_media(None)
            except Exception: pass
        except Exception:
            pass
        self.slider.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.slider.in_ms = None
        self.slider.out_ms = None
        self.slider.update()
        self._show_placeholder()
        self._loaded_path = None

    # ---- Export (Save dialog only, with options) ----
    def export_clip(self):
        if self._loaded_path is None:
            return
        if self.slider.in_ms is None or self.slider.out_ms is None or self.slider.out_ms <= self.slider.in_ms:
            self.statusBar().showMessage("Set IN/OUT first", 2500)
            return

        # zapamiętaj ustawienia checkboxów
        self.settings.setValue("use_nvdec", bool(self.chk_nvdec.isChecked()))
        self.settings.setValue("use_h265", bool(self.chk_h265.isChecked()))
        self.settings.setValue("remember_geometry", bool(self.chk_remember_geometry.isChecked()))
        self.settings.sync()

        base = self._loaded_path.stem
        suffix = ".mp4"
        default = f"{base}_{ms_to_hhmmss_ms(self.slider.in_ms).replace(':','-').replace('.', '-')}_{ms_to_hhmmss_ms(self.slider.out_ms).replace(':','-').replace('.', '-')}{suffix}"
        out_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Clip As…", str(self._loaded_path.parent / default),
            "MP4 Video (*.mp4);;All Files (*)"
        )
        if not out_path:
            return

        in_ts = f"{self.slider.in_ms/1000.0:.3f}"
        duration = f"{(self.slider.out_ms - self.slider.in_ms)/1000.0:.3f}"

        # Budowa komendy ffmpeg zależnie od opcji
        use_cuda = bool(self.chk_nvdec.isChecked())
        use_h265 = bool(self.chk_h265.isChecked())

        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "info"]
        if use_cuda:
            cmd += ["-hwaccel", "cuda"]

        cmd += ["-ss", in_ts, "-t", duration, "-i", str(self._loaded_path)]

        if use_h265:
            # re-encode H.265
            if use_cuda:
                cmd += ["-c:v", "hevc_nvenc", "-preset", "medium", "-c:a", "copy"]
            else:
                cmd += ["-c:v", "libx265", "-crf", "23", "-preset", "medium", "-c:a", "copy"]
        else:
            cmd += ["-c", "copy"]

        cmd += [out_path]

        # Progress
        self._export_prog = QtWidgets.QProgressDialog("Exporting…", "Cancel", 0, 100, self)
        self._export_prog.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
        self._export_prog.setAutoClose(True); self._export_prog.setAutoReset(True)

        self._export_proc = QtCore.QProcess(self)
        self._export_proc.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)

        time_re = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")
        total = max(0.001, (self.slider.out_ms - self.slider.in_ms)/1000.0)

        def on_out():
            data = bytes(self._export_proc.readAllStandardOutput()).decode('utf-8', 'ignore')
            for line in data.splitlines():
                m = time_re.search(line)
                if m:
                    h, m_, s, ms_ = map(int, m.groups())
                    elapsed = h*3600 + m_*60 + s + (ms_/100.0)
                    self._export_prog.setValue(int(min(100, max(0, (elapsed/total)*100))))
                    QtWidgets.QApplication.processEvents()

        def on_done(_code, _status):
            if self._export_prog:
                self._export_prog.setValue(100); self._export_prog.close()
            self.statusBar().showMessage(f"Saved: {out_path}", 4000)
            self._export_proc = None
            self._export_prog = None

        def on_cancel():
            if self._export_proc:
                self._export_proc.kill()

        self._export_proc.readyReadStandardOutput.connect(on_out)
        self._export_proc.finished.connect(on_done)
        self._export_prog.canceled.connect(on_cancel)
        self._export_proc.start(cmd[0], cmd[1:])
        self._export_prog.show()

    # ---- Close / cleanup ----
    def closeEvent(self, e: QtGui.QCloseEvent):
        # Potwierdzenie (zostawiamy)
        if getattr(self, "_confirm_on_close", True):
            res = QtWidgets.QMessageBox.question(
                self, "Confirm exit", "Do you really want to close the app?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No,
            )
            if res != QtWidgets.QMessageBox.Yes:
                e.ignore()
                return

        # zapisz ustawienia + geometrię (jeśli włączone)
        try:
            self.settings.setValue("use_nvdec", bool(self.chk_nvdec.isChecked()))
            self.settings.setValue("use_h265", bool(self.chk_h265.isChecked()))
            self.settings.setValue("remember_geometry", bool(self.chk_remember_geometry.isChecked()))
            if bool(self.chk_remember_geometry.isChecked()):
                self.settings.setValue("geometry", self.saveGeometry())
            self.settings.sync()
        except Exception:
            pass

        # Timer
        try: self.timer.stop()
        except Exception: pass
        # Eksport
        try:
            if getattr(self, "_export_proc", None) is not None:
                self._export_proc.kill()
                self._export_proc = None
            if getattr(self, "_export_prog", None) is not None:
                self._export_prog.close()
                self._export_prog = None
        except Exception: pass
        # VLC teardown
        try:
            try: self.mplayer.pause()
            except Exception: pass
            try: self.mplayer.stop()
            except Exception: pass
            try:
                if sys.platform.startswith("linux"):
                    self.mplayer.set_xwindow(0)
                elif sys.platform.startswith("win"):
                    self.mplayer.set_hwnd(0)
                else:
                    self.mplayer.set_nsobject(0)
            except Exception: pass
            try: self.mplayer.set_media(None)
            except Exception: pass
            try: self.mplayer.release()
            except Exception: pass
            try: self.vlc_instance.release()
            except Exception: pass
        except Exception: pass

        e.accept()
        super().closeEvent(e)


# ----------------------------- Entrypoint -----------------------------
def main():
    app = QtWidgets.QApplication(sys.argv)
    signal.signal(signal.SIGINT, signal.SIG_DFL)  # Ctrl+C zamyka aplikację
    initial = sys.argv[1] if len(sys.argv) > 1 else None
    w = VideoCutter(initial)
    w.show()
    try:
        sys.exit(app.exec())   # PySide6
    except AttributeError:
        sys.exit(app.exec_())  # PySide2


if __name__ == "__main__":
    main()

