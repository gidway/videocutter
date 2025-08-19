@echo off
REM ==== VideoCutter Runner for Windows ====

set SCRIPT_DIR=%~dp0
set MAIN_VIDCUTTER=%SCRIPT_DIR%\python\video_cutter.py
set VENV_DIR=%SCRIPT_DIR%\.venv
set APP_RUNNER=%VENV_DIR%\Scripts\python.exe

REM Check if Python is installed
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH.
    pause
    exit /b 1
)

REM Create virtual environment if missing
if not exist "%VENV_DIR%" (
    echo Creating virtual environment...
    python -m venv "%VENV_DIR%"
)

REM Activate venv and install requirements
echo Installing dependencies...
"%APP_RUNNER%" -m pip install --upgrade pip >nul 2>&1
"%APP_RUNNER%" -m pip install "PySide6[webengine]" python-vlc >nul 2>&1

REM Check if video_cutter.py exists
if not exist "%MAIN_VIDCUTTER%" (
    echo [ERROR] video_cutter.py not found!
    echo Expected at: %MAIN_VIDCUTTER%
    pause
    exit /b 1
)

REM Run VideoCutter
echo Starting VideoCutter...
if "%~1"=="" (
    "%APP_RUNNER%" "%MAIN_VIDCUTTER%"
) else (
    "%APP_RUNNER%" "%MAIN_VIDCUTTER%" "%~1"
)

pause
