Video Cutter - help to quick cut video into small pieces

# 🎬 VideoCutter

A lightweight GUI application (Qt + VLC + FFMPEG) for quickly cutting video into smaller clips.
Supports NVIDIA hardware acceleration (CUDA) and optional export to H.265.

---

## ✨ Features

- Play and preview videos in a window (VLC backend).
- Set **IN / OUT** points and export selected fragments.
- **Export to H.265** (optional).
- **NVIDIA CUDA acceleration** (optional).
- Saves:
  - settings (checkboxes in GUI),
  - window size (optional).
- **Keyboard shortcuts**:
  - `Space` – Play/Pause,
  - `I` – set **IN** point,
  - `O` – set **OUT** point,
  - `E` – export fragment,
  - `Ctrl+O` – open file,
  - `Ctrl+Q` – quit application,
  - `←` / `→` – frame-by-frame stepping.
- **Web banner** inside the GUI (loaded with QtWebEngine).

---

## 🚀 Installation & Run

### 1. Clone the repository
```bash
git clone https://github.com/gidway/videocutter.git
cd videocutter
```

---

## Run the application

The repository includes a Python startup script that:
- creates a virtual environment at ~/.venvs/videocutter,
- installs all required dependencies,
- launches the application.

```bash
python3 run_videocutter.py /path/to/video.mp4
```

You can also run it without arguments and choose the file from the GUI.

---

## 📦 Required packages

Installed automatically inside the virtual environment:
- PySide6[webengine]
- python-vlc

Additionally, system packages required:
- ffmpeg
- VLC (libvlc library)

On Ubuntu / Debian:

```bash
sudo apt install ffmpeg vlc python3-venv
```

---

## ⚙️ Configuration

The GUI provides the following options:
✅ Use NVIDIA CUDA (GPU acceleration when exporting).
✅ Export to H.265 (HEVC instead of H.264).
✅ Remember window size (restores on startup).

---

## 📜 Licencja

MIT
– free to use, modify and distribute.

---

// eof
