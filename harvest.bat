@echo off
setlocal
cd /d "%~dp0"

if not exist .venv\Scripts\activate.bat (
    echo Run setup.bat first.
    exit /b 1
)
call .venv\Scripts\activate.bat

echo === NHTSA ===
python -m harvest.nhtsa --force

echo === eCFR ===
python -m harvest.ecfr

echo === USPTO ===
if defined USPTO_API_KEY (
    python -m harvest.uspto
) else (
    echo USPTO_API_KEY not set - skipping patents.
    echo Set it with:  set USPTO_API_KEY=your-key   then run this file again.
)

echo === Report ===
python -m harvest.report
pause