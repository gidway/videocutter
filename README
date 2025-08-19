Video Cutter - help to quick cut video into small pieces

# 🎬 VideoCutter

Lekka aplikacja GUI (Qt + VLC + FFMPEG) do szybkiego wycinania fragmentów wideo.
Wspiera akcelerację sprzętową NVIDIA (CUDA) i możliwość eksportu do H.265.

---

## ✨ Funkcje

- Odtwarzanie i podgląd wideo w oknie (VLC backend).
- Ustawianie punktów **IN / OUT** i eksport wybranego fragmentu.
- **Eksport do H.265** (opcjonalny).
- **Akceleracja NVIDIA CUDA** (opcjonalna).
- Zapamiętywanie:
  - ustawień (checkboxy w GUI),
  - rozmiaru okna (opcjonalnie).
- Obsługa **klawiatury**:
  - `Space` – Play/Pause,
  - `I` – ustaw punkt **IN**,
  - `O` – ustaw punkt **OUT**,
  - `E` – eksport fragmentu,
  - `Ctrl+O` – otwórz plik,
  - `Ctrl+Q` – zamknij aplikację,
  - `←` / `→` – przewijanie klatka po klatce.
- **Web banner** w GUI (ładowany przez QtWebEngine).

---

## 🚀 Instalacja i uruchomienie

### 1. Klon repozytorium
```bash
git clone https://github.com/<twoje-repo>/VideoCutter.git
cd VideoCutter
```

---

## Uruchom aplikację

Do repo jest dołączony skrypt uruchomieniowy w Pythonie, który:
- tworzy wirtualne środowisko ~/.venvs/videocutter,
- instaluje wszystkie wymagane pakiety,
- uruchamia aplikację.

```bash
python3 run_videocutter.py /ścieżka/do/pliku.mp4
```

Możesz też uruchomić bez argumentu, a plik wybierzesz z GUI.

---

## 📦 Wymagane pakiety

Automatycznie instalowane w wirtualnym środowisku:
- PySide6[webengine]
- python-vlc

Dodatkowo wymagane są systemowe:
- ffmpeg
- VLC (biblioteka libvlc)

Na Ubuntu / Debian:

```bash
sudo apt install ffmpeg vlc python3-venv
```

---

## ⚙️ Konfiguracja
W GUI dostępne są opcje:
✅ Używaj NVIDIA CUDA (akceleracja GPU przy eksporcie).
✅ Eksportuj do H.265 (HEVC zamiast H.264).
✅ Zapamiętaj rozmiar okna (ustawienia przywracane po starcie).

---

## 📜 Licencja

MIT
– darmowe użycie, modyfikacja i dystrybucja.

---

// eof
