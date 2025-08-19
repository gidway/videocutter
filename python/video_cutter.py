#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Video Cutter – Qt + VLC (single-window) + PNG placeholder + WebView banner (LEFT)
+ Persistent Settings (NVIDIA hw decode, H.265 export, remember window geometry)
+ MULTI-SEGMENTS (grid) with save/load/export
+ Click-on-row: restore segment marks on the slider (and optional seek/play)

Layout:
- LEFT: Web banner (http://gidway.net/banner) – pełna wysokość, stała szerokość
- RIGHT: Video (osadzone) + Slider (full width) + Controls + Checkboxes
         + Segments table (IN/OUT/Dur/Title) + grid actions

Funkcje:
- Placeholder PNG, gdy brak/błąd wideo
- Ustawianie IN/OUT i dodawanie wielu fragmentów do siatki
- Klik w segment w tabeli → markery IN/OUT wracają na suwak; podwójny klik → odtwarzaj od startu
- Eksport: pojedynczy (z IN/OUT lub zaznaczonego w siatce) albo zbiorczy (cała siatka)
- Zapis/Odczyt siatki do pliku JSON (z metadanymi: źródłowy czas trwania)
- Opcja „Scale grid to current video” – przeskaluj czasy z siatki do długości aktualnego filmu
- Szybki eksport FFmpeg (-c copy) lub H.265 (libx265/hevc_nvenc), opcjonalnie -hwaccel cuda
- Pasek postępu przy eksporcie, auto-zamykanie
- Skróty: Space, I, O, A(dodaj segment), Del(usuń segment),
          E (eksport pojedynczy), Ctrl+E (eksport siatki),
          Ctrl+S (zapis siatki), Ctrl+L (wczytaj siatkę),
          Ctrl+O (otwórz wideo), Ctrl+Q (zamknij),
          ←/→ (klatka), Shift+←/→ (nudge)
- Klik w obszar filmu = Play/Pause
"""

import os
import re
import sys
import json
import signal
import mimetypes
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional, Tuple

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


def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


@dataclass
class Segment:
    start_ms: int
    end_ms: int
    title: str = ""

    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    def as_row(self) -> Tuple[str, str, str, str]:
        dur = self.duration_ms()
        return (
            ms_to_hhmmss_ms(self.start_ms),
            ms_to_hhmmss_ms(self.end_ms),
            ms_to_hhmmss_ms(dur),
            self.title or "",
        )


class MarkingSlider(QtWidgets.QSlider):
    """Slider z pojedynczym aktualnym zaznaczeniem IN/OUT (do tworzenia segmentów).
       (Segmenty zapisane w tabeli nie są tu malowane – trzymamy UI prosto i szybko.)"""
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
    """Kolumna banera po lewej: QWebEngineView (jeśli dostępne) lub QLabel (fallback)."""
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
        if getattr(self, "view", None) is None and self._url:
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

        # VLC core
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

        # PRZYCISKI (transport + znaczniki)
        ctr = QtWidgets.QHBoxLayout()
        hbox_right.addLayout(ctr)

        self.btn_play = QtWidgets.QPushButton("▶/⏸"); self.btn_play.clicked.connect(self.toggle); ctr.addWidget(self.btn_play)
        self.lbl_time = QtWidgets.QLabel("00:00:00.000 / 00:00:00.000"); self.lbl_time.setMinimumWidth(220); ctr.addWidget(self.lbl_time)

        self.btn_set_in = QtWidgets.QPushButton("IN (I)"); self.btn_set_in.clicked.connect(self.set_in)
        self.btn_set_out = QtWidgets.QPushButton("OUT (O)"); self.btn_set_out.clicked.connect(self.set_out)
        self.btn_add_seg = QtWidgets.QPushButton("Add segment (A)"); self.btn_add_seg.clicked.connect(self.add_segment_from_marks)
        self.btn_clear_marks = QtWidgets.QPushButton("Clear IN/OUT"); self.btn_clear_marks.clicked.connect(self.clear_marks)
        for b in (self.btn_set_in, self.btn_set_out, self.btn_add_seg, self.btn_clear_marks): ctr.addWidget(b)

        self.btn_export_single = QtWidgets.QPushButton("Export current (E)")
        self.btn_export_single.clicked.connect(self.export_current_or_selected)
        ctr.addWidget(self.btn_export_single)

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

        self.chk_scale_grid = QtWidgets.QCheckBox("Scale grid to current video")
        self.chk_scale_grid.setChecked(self.settings.value("scale_grid", False, type=bool))
        opts.addWidget(self.chk_scale_grid)

        opts.addStretch(1)

        # --- SEGMENTS TABLE + ACTIONS ---
        seg_box = QtWidgets.QVBoxLayout()
        hbox_right.addLayout(seg_box)

        self.table = QtWidgets.QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["IN", "OUT", "DURATION", "TITLE"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        our_select_mode = QtWidgets.QAbstractItemView.SingleSelection
        self.table.setSelectionMode(our_select_mode)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.EditKeyPressed)
        seg_box.addWidget(self.table)

        # >>> NEW: przywracanie ram segmentu po wyborze / podwójnym kliknięciu
        self.table.itemSelectionChanged.connect(self._on_table_selection_changed)
        self.table.cellDoubleClicked.connect(self._on_table_cell_double_clicked)
        # <<< NEW

        seg_actions = QtWidgets.QHBoxLayout()
        seg_box.addLayout(seg_actions)

        self.btn_remove_seg = QtWidgets.QPushButton("Remove (Del)")
        self.btn_remove_seg.clicked.connect(self.remove_selected_segment)
        seg_actions.addWidget(self.btn_remove_seg)

        self.btn_clear_grid = QtWidgets.QPushButton("Clear grid")
        self.btn_clear_grid.clicked.connect(self.clear_grid)
        seg_actions.addWidget(self.btn_clear_grid)

        self.btn_export_selected = QtWidgets.QPushButton("Export selected")
        self.btn_export_selected.clicked.connect(lambda: self.export_segments(selected_only=True))
        seg_actions.addWidget(self.btn_export_selected)

        self.btn_export_all = QtWidgets.QPushButton("Export ALL (Ctrl+E)")
        self.btn_export_all.clicked.connect(lambda: self.export_segments(selected_only=False))
        seg_actions.addWidget(self.btn_export_all)

        self.btn_save_grid = QtWidgets.QPushButton("Save grid (Ctrl+S)")
        self.btn_save_grid.clicked.connect(self.save_grid_to_json)
        seg_actions.addWidget(self.btn_save_grid)

        self.btn_load_grid = QtWidgets.QPushButton("Load grid (Ctrl+L)")
        self.btn_load_grid.clicked.connect(self.load_grid_from_json)
        seg_actions.addWidget(self.btn_load_grid)

        seg_actions.addStretch(1)

        # Skróty
        QShortcutCls(QKeySequence("Space"), self, self.toggle)
        QShortcutCls(QKeySequence("I"), self, self.set_in)
        QShortcutCls(QKeySequence("O"), self, self.set_out)
        QShortcutCls(QKeySequence("A"), self, self.add_segment_from_marks)
        QShortcutCls(QKeySequence("Delete"), self, self.remove_selected_segment)

        QShortcutCls(QKeySequence("E"), self, self.export_current_or_selected)
        QShortcutCls(QKeySequence("Ctrl+E"), self, lambda: self.export_segments(False))
        QShortcutCls(QKeySequence("Ctrl+S"), self, self.save_grid_to_json)
        QShortcutCls(QKeySequence("Ctrl+L"), self, self.load_grid_from_json)

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
        act_saveg = QAction("Save grid (Ctrl+S)", self); act_saveg.triggered.connect(self.save_grid_to_json); filem.addAction(act_saveg)
        act_loadg = QAction("Load grid (Ctrl+L)", self); act_loadg.triggered.connect(self.load_grid_from_json); filem.addAction(act_loadg)
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

        # grid (pamięć wewnętrzna)
        self._segments: List[Segment] = []
        self._grid_source_duration: int | None = None  # do skalowania siatki po wczytaniu

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
            "<br /><br />Hotkeys: Space (Play/Pause), I (IN), O (OUT), A (Add seg), "
            "E (Export current), Ctrl+E (Export ALL), Ctrl+S (Save grid), Ctrl+L (Load grid), "
            "←/→ (frame)"
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
        self.btn_export_single.setEnabled(True)
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
                new_t = clamp(new_t, 0, dur - 1)
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

    # ---- Marks → current selection ----
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

    # ---- NEW: pokaż segment na suwaku (+ ewentualny seek/play) ----
    def _show_segment_on_slider(self, seg: Segment, seek: bool = True, pause: bool = True):
        """Ustaw markery IN/OUT na suwaku wg segmentu i (opcjonalnie) przeskocz do startu."""
        if seg is None:
            return
        self.slider.in_ms = int(seg.start_ms)
        self.slider.out_ms = int(seg.end_ms)
        self.slider.update()

        if seek and self._loaded_path is not None:
            try:
                self.mplayer.pause()
            except Exception:
                pass
            self.mplayer.set_time(int(seg.start_ms))
            if pause:
                self._force_redraw(self._frame_ms // 2)
            else:
                self.mplayer.play()

        # zaktualizuj label czasu i pozycję suwaka
        if seek:
            self.slider.setValue(int(seg.start_ms))
            self._update_time_label(int(seg.start_ms), self._duration_ms)

    # ---- Segments grid ----
    def add_segment_from_marks(self):
        if self._duration_ms <= 0:
            self.statusBar().showMessage("Load a video first.", 2500)
            return
        if self.slider.in_ms is None or self.slider.out_ms is None or self.slider.out_ms <= self.slider.in_ms:
            self.statusBar().showMessage("Set valid IN/OUT first.", 2500)
            return

        seg = Segment(start_ms=int(self.slider.in_ms), end_ms=int(self.slider.out_ms), title="")
        self._segments.append(seg)
        self._append_segment_row(seg)

        # NEW: automatycznie zaznacz dopisany segment i przywróć jego ramy
        r = self.table.rowCount() - 1
        if r >= 0:
            self.table.selectRow(r)
            self._show_segment_on_slider(seg, seek=True, pause=True)

        # auto-clear marks dla szybkiego znakowania
        self.clear_marks()

    def _append_segment_row(self, seg: Segment):
        r = self.table.rowCount()
        self.table.insertRow(r)
        in_item  = QtWidgets.QTableWidgetItem(seg.as_row()[0]); in_item.setFlags(in_item.flags() & ~Qt.ItemIsEditable)
        out_item = QtWidgets.QTableWidgetItem(seg.as_row()[1]); out_item.setFlags(out_item.flags() & ~Qt.ItemIsEditable)
        dur_item = QtWidgets.QTableWidgetItem(seg.as_row()[2]); dur_item.setFlags(dur_item.flags() & ~Qt.ItemIsEditable)
        ttl_item = QtWidgets.QTableWidgetItem(seg.as_row()[3])

        self.table.setItem(r, 0, in_item)
        self.table.setItem(r, 1, out_item)
        self.table.setItem(r, 2, dur_item)
        self.table.setItem(r, 3, ttl_item)

    def _refresh_table(self):
        self.table.setRowCount(0)
        for s in self._segments:
            self._append_segment_row(s)

    def _selected_row_index(self) -> Optional[int]:
        rows = self.table.selectionModel().selectedRows()
        if rows:
            return rows[0].row()
        return None

    # ---- NEW: reakcje tabeli na wybór / podwójny klik ----
    def _on_table_selection_changed(self):
        idx = self._selected_row_index()
        if idx is None:
            return
        if 0 <= idx < len(self._segments):
            seg = self._segments[idx]
            # pokaż markery na suwaku, przeskocz i zostaw spauzowane
            self._show_segment_on_slider(seg, seek=True, pause=True)

    def _on_table_cell_double_clicked(self, row: int, col: int):
        if 0 <= row < len(self._segments):
            seg = self._segments[row]
            # podwójny klik – odtwórz od początku segmentu
            self._show_segment_on_slider(seg, seek=True, pause=False)

    def remove_selected_segment(self):
        idx = self._selected_row_index()
        if idx is None:
            return
        if 0 <= idx < len(self._segments):
            del self._segments[idx]
            self.table.removeRow(idx)

    def clear_grid(self):
        self._segments.clear()
        self.table.setRowCount(0)

    # ---- Save/Load grid (JSON) ----
    def save_grid_to_json(self):
        if not self._segments:
            self.statusBar().showMessage("Grid is empty.", 2500)
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save grid as…", str(self._loaded_path.parent if self._loaded_path else Path.home() / "segments.json"),
            "JSON files (*.json);;All files (*)"
        )
        if not path:
            return
        data = {
            "source_file": str(self._loaded_path) if self._loaded_path else None,
            "source_duration_ms": int(self._duration_ms),
            "segments": [asdict(s) for s in self._segments],
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.statusBar().showMessage(f"Grid saved: {path}", 3000)
        except Exception as e:
            self.statusBar().showMessage(f"Failed to save grid: {e}", 4000)

    def load_grid_from_json(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load grid…", str(self._loaded_path.parent if self._loaded_path else Path.home()),
            "JSON files (*.json);;All files (*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            segs = []
            for d in data.get("segments", []):
                s = Segment(start_ms=int(d["start_ms"]), end_ms=int(d["end_ms"]), title=str(d.get("title", "")))
                segs.append(s)

            self._segments = segs
            self._refresh_table()
            self._grid_source_duration = int(data.get("source_duration_ms", 0))
            self.statusBar().showMessage(f"Grid loaded: {path}", 3000)
        except Exception as e:
            self.statusBar().showMessage(f"Failed to load grid: {e}", 4000)

    # ---- Error / cleanup ----
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
        self.btn_export_single.setEnabled(False)
        self.clear_marks()
        self._show_placeholder()
        self._loaded_path = None

    # ---- Export helpers ----
    def _ffmpeg_cmd(self, in_ts: float, duration_s: float, input_path: str, out_path: str) -> list:
        use_cuda = bool(self.chk_nvdec.isChecked())
        use_h265 = bool(self.chk_h265.isChecked())

        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "info"]
        if use_cuda:
            cmd += ["-hwaccel", "cuda"]

        cmd += ["-ss", f"{in_ts:.3f}", "-t", f"{duration_s:.3f}", "-i", input_path]

        if use_h265:
            if use_cuda:
                cmd += ["-c:v", "hevc_nvenc", "-preset", "medium", "-c:a", "copy"]
            else:
                cmd += ["-c:v", "libx265", "-crf", "23", "-preset", "medium", "-c:a", "copy"]
        else:
            cmd += ["-c", "copy"]

        cmd += [out_path]
        return cmd

    def _run_ffmpeg_with_progress(self, cmd: list, total_seconds: float, done_msg: str):
        # Progress
        prog = QtWidgets.QProgressDialog("Exporting…", "Cancel", 0, 100, self)
        prog.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
        prog.setAutoClose(True); prog.setAutoReset(True)

        proc = QtCore.QProcess(self)
        proc.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)

        time_re = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")
        total = max(0.001, float(total_seconds))

        def on_out():
            data = bytes(proc.readAllStandardOutput()).decode('utf-8', 'ignore')
            for line in data.splitlines():
                m = time_re.search(line)
                if m:
                    h, m_, s, ms_ = map(int, m.groups())
                    elapsed = h*3600 + m_*60 + s + (ms_/100.0)
                    prog.setValue(int(min(100, max(0, (elapsed/total)*100))))
                    QtWidgets.QApplication.processEvents()

        def on_done(_code, _status):
            prog.setValue(100); prog.close()
            self.statusBar().showMessage(done_msg, 4000)

        def on_cancel():
            proc.kill()

        proc.readyReadStandardOutput.connect(on_out)
        proc.finished.connect(on_done)
        prog.canceled.connect(on_cancel)
        proc.start(cmd[0], cmd[1:])
        prog.show()

        # zapamiętaj ustawienia checkboxów
        try:
            self.settings.setValue("use_nvdec", bool(self.chk_nvdec.isChecked()))
            self.settings.setValue("use_h265", bool(self.chk_h265.isChecked()))
            self.settings.setValue("remember_geometry", bool(self.chk_remember_geometry.isChecked()))
            self.settings.setValue("scale_grid", bool(self.chk_scale_grid.isChecked()))
            self.settings.sync()
        except Exception:
            pass

    # ---- Export single (from marks or from selected row) ----
    def export_current_or_selected(self):
        if self._loaded_path is None:
            return

        # priorytet: wybrany wiersz siatki
        idx = self._selected_row_index()
        if idx is not None and 0 <= idx < len(self._segments):
            seg = self._segments[idx]
            in_ms, out_ms = seg.start_ms, seg.end_ms
            base = (seg.title or f"{self._loaded_path.stem}_seg{idx+1}").strip().replace(" ", "_")
        else:
            # fallback: aktualne IN/OUT
            if self.slider.in_ms is None or self.slider.out_ms is None or self.slider.out_ms <= self.slider.in_ms:
                self.statusBar().showMessage("Set IN/OUT or select a segment.", 2500)
                return
            in_ms, out_ms = int(self.slider.in_ms), int(self.slider.out_ms)
            base = f"{self._loaded_path.stem}_{ms_to_hhmmss_ms(in_ms).replace(':','-').replace('.', '-')}_{ms_to_hhmmss_ms(out_ms).replace(':','-').replace('.', '-')}"

        out_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Clip As…", str(self._loaded_path.parent / (base + ".mp4")),
            "MP4 Video (*.mp4);;All Files (*)"
        )
        if not out_path:
            return

        duration_s = (out_ms - in_ms) / 1000.0
        cmd = self._ffmpeg_cmd(in_ms/1000.0, duration_s, str(self._loaded_path), out_path)
        self._run_ffmpeg_with_progress(cmd, duration_s, f"Saved: {out_path}")

    # ---- Export segments (selected/all, with optional scaling) ----
    def export_segments(self, selected_only: bool):
        if self._loaded_path is None:
            return
        if not self._segments:
            self.statusBar().showMessage("Grid is empty.", 2500)
            return

        rows = []
        if selected_only:
            idx = self._selected_row_index()
            if idx is None:
                self.statusBar().showMessage("Select a segment first.", 2500)
                return
            rows = [idx]
        else:
            rows = list(range(len(self._segments)))

        # katalog docelowy
        out_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select output directory", str(self._loaded_path.parent)
        )
        if not out_dir:
            return
        out_dir = Path(out_dir)

        # przygotuj skalowanie
        scale = bool(self.chk_scale_grid.isChecked())
        src_dur = getattr(self, "_grid_source_duration", None)
        cur_dur = int(self._duration_ms)

        for i in rows:
            seg = self._segments[i]
            s_ms, e_ms = seg.start_ms, seg.end_ms
            if scale and src_dur and src_dur > 0 and cur_dur > 0:
                # przewalutuj segment z czasu źródłowego na bieżący
                ratio = cur_dur / float(src_dur)
                s_ms = int(round(s_ms * ratio))
                e_ms = int(round(e_ms * ratio))
                s_ms = clamp(s_ms, 0, max(0, cur_dur-1))
                e_ms = clamp(e_ms, 0, max(0, cur_dur))
                if e_ms <= s_ms:
                    e_ms = min(cur_dur, s_ms + 1)

            duration_s = (e_ms - s_ms) / 1000.0
            # nazwa pliku
            base_title = seg.title.strip().replace(" ", "_") if seg.title else f"seg{i+1}"
            fname = f"{self._loaded_path.stem}_{base_title}_{ms_to_hhmmss_ms(s_ms).replace(':','-').replace('.', '-')}_{ms_to_hhmmss_ms(e_ms).replace(':','-').replace('.', '-')}.mp4"
            out_path = str(out_dir / fname)

            cmd = self._ffmpeg_cmd(s_ms/1000.0, duration_s, str(self._loaded_path), out_path)
            self._run_ffmpeg_with_progress(cmd, duration_s, f"Saved: {out_path}")

    # ---- Close / cleanup ----
    def closeEvent(self, e: QtGui.QCloseEvent):
        # Potwierdzenie
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
            self.settings.setValue("scale_grid", bool(self.chk_scale_grid.isChecked()))
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
