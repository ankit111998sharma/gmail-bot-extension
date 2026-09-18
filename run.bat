@echo off
setlocal EnableExtensions
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo Python 3.11+ is required on PATH.
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv .venv
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

if not exist "data\gmail" mkdir "data\gmail"
if not exist "data\knowledge" mkdir "data\knowledge"
if not exist "logs" mkdir "logs"

echo Starting Gmail Draft Assistant...
echo Drafts only — this app never sends mail.
python launcher.py
