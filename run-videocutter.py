#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Launcher for VideoCutter:
- ensures venv at ~/.venvs/videocutter
- installs PySide6[webengine] and python-vlc into the venv
- verifies system deps: ffmpeg/ffprobe, libVLC (via vlc --version or python-vlc probe)
- sets env (QTWEBENGINE_DISABLE_SANDBOX=1, QT_QPA_PLATFORM=xcb)
- runs python/video_cutter.py with optional video arg

Usage:
  python3 run_videocutter.py [--debug] [/path/to/video.ext]
"""

import argparse
import os
import subprocess
import sys
import shutil
from pathlib import Path

VENV_DIR = Path.home() / ".venvs" / "videocutter"
PIP_PACKAGES = ["PySide6[webengine]", "python-vlc"]

# env (jak w Twoim bash)
ENV_EXPORT = {
    "QTWEBENGINE_DISABLE_SANDBOX": "1",
    "QT_QPA_PLATFORM": "xcb",
    # te dwie są „ciche” dla libva, jeżeli chcesz – możesz zakomentować:
    "LIBVA_MESSAGING_LEVEL": "0",
    "LIBVA_DRIVER_NAME": "dummy",
}

def script_root() -> Path:
    """Folder, w którym leży ten launcher."""
    return Path(__file__).resolve().parent

def videocutter_main_path() -> Path:
    """Ścieżka do python/video_cutter.py (wg Twojej struktury)."""
    return script_root() / "python" / "video_cutter.py"

def ensure_venv(debug: bool = False) -> Path:
    """Utwórz venv jeśli nie istnieje. Zwraca ścieżkę do interpretera z venv."""
    py = sys.executable  # aktualny interpreter
    if not VENV_DIR.exists():
        if debug:
            print(f"[DEBUG] Creating venv: {VENV_DIR}")
        VENV_DIR.parent.mkdir(parents=True, exist_ok=True)
        subprocess.check_call([py, "-m", "venv", str(VENV_DIR)])
    # interpreter w venv:
    python_bin = VENV_DIR / "bin" / "python"
    if not python_bin.exists():
        # czasem Windows/inna lokalizacja, ale na Ubuntu to OK
        raise FileNotFoundError(f"Python in venv not found: {python_bin}")
    return python_bin

def pip_install(python_bin: Path, debug: bool = False):
    """Zainstaluj wymagane paczki do venv."""
    cmd = [str(python_bin), "-m", "pip", "install", "--upgrade", "pip"]
    subprocess.check_call(cmd, stdout=None if debug else subprocess.DEVNULL, stderr=None if debug else subprocess.DEVNULL)

    cmd = [str(python_bin), "-m", "pip", "install"] + PIP_PACKAGES
    if debug:
        print(f"[DEBUG] Installing packages: {' '.join(PIP_PACKAGES)}")
        subprocess.check_call(cmd)
    else:
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def check_system_deps(debug: bool = False) -> bool:
    """
    Sprawdź ffmpeg/ffprobe i libVLC.
    Zwraca True jeśli wszystko OK, False jeżeli coś brakuje (wypisuje tipy).
    """
    ok = True

    def has_cmd(cmd):
        return shutil.which(cmd) is not None

    # ffmpeg / ffprobe
    if not has_cmd("ffmpeg"):
        print("ERROR: Missing 'ffmpeg'. On Ubuntu: sudo apt install ffmpeg")
        ok = False
    if not has_cmd("ffprobe"):
        # zwykle razem z ffmpeg, ale sprawdźmy
        print("ERROR: Missing 'ffprobe'. On Ubuntu: sudo apt install ffmpeg")
        ok = False

    # VLC / libVLC – minimum to zainstalowana biblioteka (zwykle pakiet 'vlc')
    if not has_cmd("vlc"):
        # nie zawsze musi być binarka 'vlc', ale to szybki check
        print("WARNING: 'vlc' command not found. 'python-vlc' needs libVLC runtime.")
        print("         On Ubuntu: sudo apt install vlc")
        # nie oznaczajmy tego jako fatal – spróbujemy jeszcze załadować python-vlc później
    return ok

def probe_webengine(python_bin: Path, debug: bool = False) -> bool:
    """Sprawdź czy w venv działa QWebEngineView (informacyjnie)."""
    code = "from PySide6.QtWebEngineWidgets import QWebEngineView; print('OK')"
    try:
        out = subprocess.check_output([str(python_bin), "-c", code], stderr=subprocess.STDOUT, text=True)
        if debug:
            print(f"[DEBUG] WebEngine probe output: {out.strip()}")
        return "OK" in out
    except subprocess.CalledProcessError as e:
        if debug:
            print("[DEBUG] WebEngine probe failed:", e.output)
        return False
    except Exception as e:
        if debug:
            print("[DEBUG] WebEngine probe error:", repr(e))
        return False

def run_app(python_bin: Path, video_path: str | None, debug: bool = False) -> int:
    main_py = videocutter_main_path()
    if not main_py.exists():
        print("ERROR: can't find main app:", main_py)
        return 2

    # jeśli podano wideo – sprawdź istnienie
    if video_path:
        vp = Path(video_path).expanduser()
        if not vp.exists():
            print("ERROR: can't find video file:", vp)
            return 3

    # środowisko runtime
    env = os.environ.copy()
    env.update(ENV_EXPORT)

    argv = [str(python_bin), str(main_py)]
    if video_path:
        argv.append(video_path)

    if debug:
        print(f"[DEBUG] Launch: {' '.join(argv)}")
        print(f"[DEBUG] ENV add: {ENV_EXPORT}")

    try:
        return subprocess.call(argv, env=env)
    except KeyboardInterrupt:
        return 130

def main():
    ap = argparse.ArgumentParser(description="VideoCutter launcher (venv + deps + run)")
    ap.add_argument("video", nargs="?", help="Optional path to a video file")
    ap.add_argument("--debug", action="store_true", help="Verbose output")
    args = ap.parse_args()

    # krok 1: sprawdź systemowe zależności
    sys_ok = check_system_deps(debug=args.debug)
    if not sys_ok:
        # dalej spróbujemy, ale użytkownik wie co doinstalować
        pass

    # krok 2: venv + pip install
    try:
        pybin = ensure_venv(debug=args.debug)
    except Exception as e:
        print("ERROR: could not create/find venv:", e)
        return 10

    try:
        pip_install(pybin, debug=args.debug)
    except subprocess.CalledProcessError as e:
        print("ERROR: pip install failed.")
        if args.debug:
            print("Details:", e)
        return 11

    # krok 3 (opcjonalny): sonda WebEngine
    web_ok = probe_webengine(pybin, debug=args.debug)
    if args.debug:
        print(f"[DEBUG] Qt WebEngine availability: {'OK' if web_ok else 'MISSING'}")

    # krok 4: uruchom aplikację
    rc = run_app(pybin, args.video, debug=args.debug)
    if rc not in (0, None):
        return rc
    return 0

if __name__ == "__main__":
    sys.exit(main())


