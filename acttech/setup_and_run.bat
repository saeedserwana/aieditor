@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ============================================================
REM  Local IDE Updater - Setup + Run (Windows)
REM  - Creates .venv if missing
REM  - Activates venv
REM  - Upgrades pip/setuptools/wheel
REM  - Installs requirements (with a small cache)
REM  - Optional: checks config.py exists
REM  - Starts FastAPI server
REM ============================================================

REM ---- Go to this script folder ----
cd /d "%~dp0"

title Local IDE Updater - Setup & Run

echo.
echo ============================================================
echo   Local IDE Updater - Setup & Run
echo   Folder: %cd%
echo ============================================================
echo.

REM ---- Choose python (py preferred) ----
set "PYEXE="
where py >nul 2>nul
if %errorlevel%==0 (
  set "PYEXE=py"
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    set "PYEXE=python"
  ) else (
    echo [ERROR] Python not found. Install Python 3.10+ and re-run.
    pause
    exit /b 1
  )
)

REM ---- Ensure config.py exists ----
if not exist "config.py" (
  echo [ERROR] config.py not found in %cd%
  echo Create config.py first (it is the only file you should edit).
  pause
  exit /b 1
)

REM ---- Create venv if missing ----
if not exist ".venv\Scripts\python.exe" (
  echo [1/6] Creating virtual environment (.venv)...
  %PYEXE% -m venv .venv
  if %errorlevel% neq 0 (
    echo [ERROR] Failed to create venv.
    pause
    exit /b 1
  )
) else (
  echo [1/6] Virtual environment already exists.
)

REM ---- Activate venv ----
echo [2/6] Activating venv...
call ".venv\Scripts\activate.bat"
if %errorlevel% neq 0 (
  echo [ERROR] Failed to activate venv.
  pause
  exit /b 1
)

REM ---- Show python version ----
echo [3/6] Python:
python --version
echo.

REM ---- Upgrade pip tools ----
echo [4/6] Upgrading pip/setuptools/wheel...
python -m pip install --upgrade pip setuptools wheel
if %errorlevel% neq 0 (
  echo [ERROR] Failed to upgrade pip tools.
  pause
  exit /b 1
)

REM ---- Install requirements (fast + repeatable) ----
echo [5/6] Installing requirements...
if not exist "requirements.txt" (
  echo [ERROR] requirements.txt not found.
  pause
  exit /b 1
)

REM Use a local pip cache folder for speed (optional)
if not exist ".pip-cache" mkdir ".pip-cache" >nul 2>nul

pip install --cache-dir ".pip-cache" -r requirements.txt
if %errorlevel% neq 0 (
  echo [ERROR] Failed to install requirements.
  echo Try: pip install -r requirements.txt --no-cache-dir
  pause
  exit /b 1
)

REM ---- Defaults (override by env vars) ----
if "%HOST%"=="" set "HOST=127.0.0.1"
if "%PORT%"=="" set "PORT=8787"

echo [6/6] Starting server...
echo.
echo   URL:  http://%HOST%:%PORT%
echo   Stop: CTRL+C
echo.

REM ---- Run server ----
python -m uvicorn web_app:app --host %HOST% --port %PORT%

REM Keep window open if it exits
echo.
echo Server stopped.
pause
