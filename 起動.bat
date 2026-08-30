@echo off
chcp 65001 > nul
cd /d "%~dp0"
python chest_auto.py
if errorlevel 1 (
  echo.
  echo ---- error ----
  echo If modules are missing, run: pip install -r requirements.txt
  pause
)
