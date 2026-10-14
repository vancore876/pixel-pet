@echo off
setlocal
cd /d "%~dp0"
if not exist ".server-venv\Scripts\python.exe" (
    echo First run: creating the work server's private Python environment...
    py -3 -m venv .server-venv
    if errorlevel 1 (
        python -m venv .server-venv
        if errorlevel 1 goto :no_python
    )
)
".server-venv\Scripts\python.exe" -c "import struct, sys; raise SystemExit(0 if sys.version_info >= (3, 11) and struct.calcsize('P') == 8 else 1)"
if errorlevel 1 goto :no_python
".server-venv\Scripts\python.exe" -m pip install -r requirements-server.txt
if errorlevel 1 goto :install_error
".server-venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto :install_error
echo Work server dependencies are ready.
exit /b 0
:no_python
echo Install 64-bit Python 3.11 or newer from https://www.python.org/downloads/windows/
echo Choose the option to add Python to PATH, then run this file again.
echo If you copied a .server-venv from another PC, use a fresh project folder instead.
pause
exit /b 1
:install_error
echo Installation failed. Check your Internet connection and Python version.
pause
exit /b 1
