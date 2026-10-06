@echo off
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
title MusicBot
cd /d "%~dp0"

rem --- check venv, create if missing ---
if not exist ".venv\Scripts\python.exe" (
    echo [i] venv not found, creating...
    set PY=python
    python --version >nul 2>nul || set PY=py -3
    %PY% -m venv .venv
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [!] pip install failed
        pause
        exit /b 1
    )
)

rem --- check .env ---
if not exist ".env" (
    echo [!] .env not found! Create it with BOT_TOKEN=your_token
    pause
    exit /b 1
)

echo ================================================
echo   MusicBot starting...  (Ctrl+C = stop)
echo ================================================
.venv\Scripts\python.exe bot.py

echo.
echo Bot exited (code %errorlevel%).
pause