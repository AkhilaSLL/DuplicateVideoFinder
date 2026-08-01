@echo off
REM ===================================================================
REM  Duplicate Video Finder - Windows build script
REM  Double-click this file, or run it in a Command Prompt.
REM  Requires Python 3.10+ on PATH (python.org installer).
REM  Produces:  dist\DuplicateVideoFinder.exe   (single standalone file)
REM ===================================================================

setlocal
cd /d "%~dp0"

echo.
echo [1/5] Checking Python...
python --version
if errorlevel 1 (
    echo  ERROR: Python was not found on PATH.
    echo  Install it from https://www.python.org/downloads/ and tick
    echo  "Add Python to PATH" during installation, then re-run this.
    pause
    exit /b 1
)

echo.
echo [2/5] Creating virtual environment (.venv)...
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 (
    echo  ERROR: could not create the virtual environment.
    pause
    exit /b 1
)
set "PY=.venv\Scripts\python.exe"

echo.
echo [3/5] Installing dependencies...
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo  ERROR: dependency installation failed.
    pause
    exit /b 1
)

echo.
echo [4/5] Generating app icon...
if not exist "assets\app.ico" "%PY%" scripts\make_icon.py

echo.
echo [5/5] Building DuplicateVideoFinder.exe ...
"%PY%" -m PyInstaller --noconfirm --clean ^
    --distpath dist --workpath build ^
    packaging\DuplicateVideoFinder.spec
if errorlevel 1 (
    echo  ERROR: PyInstaller build failed.
    pause
    exit /b 1
)

echo.
echo ===================================================================
echo  DONE.  Your program is here:
echo     %cd%\dist\DuplicateVideoFinder.exe
echo  You can copy that single .exe anywhere and run it.
echo ===================================================================
pause
endlocal
