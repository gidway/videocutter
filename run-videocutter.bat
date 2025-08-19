@echo off
setlocal EnableExtensions EnableDelayedExpansion
REM ===========================================================
REM   VideoCutter Portable Launcher (Windows)
REM   - Creates .venv in project dir
REM   - Installs PySide6[webengine] + python-vlc
REM   - Ensures portable FFmpeg (download if missing)
REM   - Ensures portable VLC (download if missing)
REM   - Runs python\video_cutter.py with optional video arg
REM ===========================================================

REM --- Paths ---
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "MAIN_VIDCUTTER=%SCRIPT_DIR%\python\video_cutter.py"
set "VENV_DIR=%SCRIPT_DIR%\.venv"
set "APP_PY=%VENV_DIR%\Scripts\python.exe"
set "TOOLS_DIR=%SCRIPT_DIR%\tools"
set "FFMPEG_DIR=%TOOLS_DIR%\ffmpeg"
set "VLC_DIR=%TOOLS_DIR%\vlc"
set "FFMPEG_BIN="
set "VLC_BIN="

REM --- Optional debug: set DEBUG=1 for verbose PowerShell output
set "DEBUG="

REM ===========================================================
REM  Helper: run PowerShell inline
REM ===========================================================
set "PS=powershell -NoProfile -ExecutionPolicy Bypass -Command"

REM ===========================================================
REM  Check Python
REM ===========================================================
where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python is not installed or not in PATH.
  echo Download: https://www.python.org/downloads/windows/
  pause
  exit /b 1
)

REM ===========================================================
REM  Ensure portable FFmpeg (download if missing)
REM  - Source: stable "release-essentials" zip (gyan.dev)
REM ===========================================================
call :ensure_ffmpeg
if errorlevel 1 (
  echo [ERROR] Failed to ensure FFmpeg.
  pause
  exit /b 2
)

REM ===========================================================
REM  Ensure portable VLC (download if missing)
REM  - Source: videolan "last/win64" zip (parse directory listing)
REM ===========================================================
call :ensure_vlc
if errorlevel 1 (
  echo [ERROR] Failed to ensure VLC (libVLC).
  pause
  exit /b 3
)

REM ===========================================================
REM  Create venv (if needed) and install deps
REM ===========================================================
if not exist "%VENV_DIR%" (
  echo [+] Creating virtual environment...
  python -m venv "%VENV_DIR%"
  if errorlevel 1 (
    echo [ERROR] Failed to create venv at %VENV_DIR%
    pause
    exit /b 4
  )
)

echo [+] Installing Python dependencies (PySide6[webengine], python-vlc)...
"%APP_PY%" -m pip install --upgrade pip >nul 2>&1
if defined DEBUG "%APP_PY%" -m pip install "PySide6[webengine]" python-vlc
if not defined DEBUG "%APP_PY%" -m pip install "PySide6[webengine]" python-vlc >nul 2>&1
if errorlevel 1 (
  echo [ERROR] pip install failed.
  pause
  exit /b 5
)

REM ===========================================================
REM  Check app file
REM ===========================================================
if not exist "%MAIN_VIDCUTTER%" (
  echo [ERROR] Cannot find app: %MAIN_VIDCUTTER%
  pause
  exit /b 6
)

REM ===========================================================
REM  Prepare PATH (portable ffmpeg + vlc first)
REM ===========================================================
if exist "%FFMPEG_BIN%" set "PATH=%FFMPEG_BIN%;%PATH%"
if exist "%VLC_DIR%"    set "PATH=%VLC_DIR%;%PATH%"

REM  You generally **do not** need to set Qt vars on Windows.
REM  If you run into WebEngine GPU issues, you might experiment with:
REM     set QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu-sandbox

REM ===========================================================
REM  Run app
REM ===========================================================
echo [+] Starting VideoCutter...
if "%~1"=="" (
  "%APP_PY%" "%MAIN_VIDCUTTER%"
) else (
  "%APP_PY%" "%MAIN_VIDCUTTER%" "%~1"
)

goto :eof

REM ===========================================================
REM  Functions
REM ===========================================================

:ensure_ffmpeg
  REM 1) If ffmpeg in PATH already, prefer system one
  where ffmpeg >nul 2>&1
  if %errorlevel%==0 (
    for /f "delims=" %%P in ('where ffmpeg') do (
      echo [i] System FFmpeg found: %%P
      goto :ffmpeg_ok
    )
  )

  REM 2) Try portable in tools\ffmpeg
  if exist "%FFMPEG_DIR%" (
    for /f "delims=" %%f in ('dir /b /s "%FFMPEG_DIR%\ffmpeg.exe" 2^>nul') do (
      set "FFMPEG_BIN=%%~dpf"
      goto :ffmpeg_ok
    )
  )

  REM 3) Download portable FFmpeg release-essentials (stable URL)
  echo [+] Downloading portable FFmpeg (release-essentials)...
  if not exist "%TOOLS_DIR%" mkdir "%TOOLS_DIR%" >nul 2>&1
  if exist "%FFMPEG_DIR%" rmdir /s /q "%FFMPEG_DIR%" >nul 2>&1
  mkdir "%FFMPEG_DIR%" >nul 2>&1

  set "FF_URL=https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
  set "FF_ZIP=%TOOLS_DIR%\ffmpeg.zip"

  if defined DEBUG (
    %PS% "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest '%FF_URL%' -OutFile '%FF_ZIP%'"
  ) else (
    %PS% "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest '%FF_URL%' -OutFile '%FF_ZIP%'" >nul 2>&1
  )
  if errorlevel 1 exit /b 1

  if defined DEBUG (
    %PS% "Expand-Archive -Path '%FF_ZIP%' -DestinationPath '%FFMPEG_DIR%' -Force"
  ) else (
    %PS% "Expand-Archive -Path '%FF_ZIP%' -DestinationPath '%FFMPEG_DIR%' -Force" >nul 2>&1
  )
  if errorlevel 1 exit /b 1

  del /f /q "%FF_ZIP%" >nul 2>&1

  REM Find ffmpeg.exe inside extracted tree
  set "FFMPEG_BIN="
  for /f "delims=" %%f in ('dir /b /s "%FFMPEG_DIR%\ffmpeg.exe" 2^>nul') do (
    set "FFMPEG_BIN=%%~dpf"
    goto :ffmpeg_ok
  )

  echo [ERROR] FFmpeg not found after extraction.
  exit /b 1

:ffmpeg_ok
  if defined FFMPEG_BIN (
    echo [i] Portable FFmpeg: %FFMPEG_BIN%
  )
  exit /b 0


:ensure_vlc
  REM 1) If libvlc in PATH (system VLC), use it
  where vlc >nul 2>&1
  if %errorlevel%==0 (
    for /f "delims=" %%P in ('where vlc') do (
      echo [i] System VLC found: %%P
      goto :vlc_ok
    )
  )

  REM 2) Try portable in tools\vlc
  if exist "%VLC_DIR%\libvlc.dll" (
    goto :vlc_ok
  )

  REM 3) Download latest portable VLC zip from 'last/win64'
  echo [+] Downloading portable VLC (latest win64)...
  if not exist "%TOOLS_DIR%" mkdir "%TOOLS_DIR%" >nul 2>&1
  if exist "%VLC_DIR%" rmdir /s /q "%VLC_DIR%" >nul 2>&1
  mkdir "%VLC_DIR%" >nul 2>&1

  set "VLC_INDEX=https://download.videolan.org/pub/videolan/vlc/last/win64/"
  set "VLC_ZIP=%TOOLS_DIR%\vlc.zip"

  REM Fetch index HTML and extract first 'vlc-*-win64.zip' link
  for /f "usebackq delims=" %%L in (`powershell -NoProfile -ExecutionPolicy Bypass ^
    "$ErrorActionPreference='Stop';" ^
    "[Net.ServicePointManager]::SecurityProtocol='Tls12';" ^
    "$u='%VLC_INDEX%';" ^
    "$h=(Invoke-WebRequest -Uri $u).Links | Where-Object href -match 'vlc-.*-win64\.zip$' | Select-Object -First 1 -ExpandProperty href;" ^
    "if(-not $h){throw 'No zip link found'};" ^
    "Write-Output ($u + $h)"`) do (
      set "VLC_URL=%%L"
  )
  if not defined VLC_URL (
    echo [ERROR] Unable to find VLC zip link.
    exit /b 1
  )

  if defined DEBUG (
    %PS% "Invoke-WebRequest '%VLC_URL%' -OutFile '%VLC_ZIP%'"
  ) else (
    %PS% "Invoke-WebRequest '%VLC_URL%' -OutFile '%VLC_ZIP%'" >nul 2>&1
  )
  if errorlevel 1 exit /b 1

  if defined DEBUG (
    %PS% "Expand-Archive -Path '%VLC_ZIP%' -DestinationPath '%VLC_DIR%' -Force"
  ) else (
    %PS% "Expand-Archive -Path '%VLC_ZIP%' -DestinationPath '%VLC_DIR%' -Force" >nul 2>&1
  )
  if errorlevel 1 exit /b 1

  del /f /q "%VLC_ZIP%" >nul 2>&1

  REM Optionally flatten if it extracted into a subfolder "vlc-*-win64"
  for /f "delims=" %%d in ('dir /b /ad "%VLC_DIR%"') do (
    if exist "%VLC_DIR%\%%d\libvlc.dll" (
      xcopy /E /I /Y "%VLC_DIR%\%%d\*" "%VLC_DIR%\" >nul
      rmdir /s /q "%VLC_DIR%\%%d" >nul 2>&1
      goto :vlc_ok
    )
  )

  if not exist "%VLC_DIR%\libvlc.dll" (
    echo [ERROR] libvlc.dll not found after extraction.
    exit /b 1
  )

:vlc_ok
  echo [i] Portable/system VLC ready.
  exit /b 0
