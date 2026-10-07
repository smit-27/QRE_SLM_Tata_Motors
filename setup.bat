@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo Python not found. Install Python 3.10+ from python.org and tick "Add python.exe to PATH".
    exit /b 1
)

if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 exit /b 1
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
    echo Package install failed. Check your internet or proxy settings.
    exit /b 1
)

python -m harvest.selftest
python -m harvest.netcheck

echo.
echo Free disk space on this drive:
dir /-c "%~d0\" | find "bytes free"
pause