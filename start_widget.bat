@echo off
title TwinRails
cd /d "%~dp0"

set "PYTHON="
py -3 -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 set "PYTHON=py -3"

if not defined PYTHON (
    python -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>&1
    if not errorlevel 1 set "PYTHON=python"
)

if not defined PYTHON (
    echo Error: Python 3.10 or later is not installed or not available on PATH.
    echo Install Python 3.10 or later from https://www.python.org/downloads/windows/
    echo and select "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)

%PYTHON% -c "import pystray, PIL, curl_cffi, requests, urllib3, psutil" >nul 2>&1
if errorlevel 1 (
    echo Installing required packages. This only happens when packages are missing.
    %PYTHON% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Installation failed. Check your internet connection, then run:
        echo   %PYTHON% -m pip install -r requirements.txt
        pause
        exit /b 1
    )
)

echo Starting TwinRails...
%PYTHON% main.py %*
