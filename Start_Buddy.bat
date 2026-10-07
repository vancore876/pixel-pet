@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo First run: creating a private Python environment...
    py -3 -m venv .venv
    if errorlevel 1 (
        python -m venv .venv
        if errorlevel 1 goto :no_python
    )
)
".venv\Scripts\python.exe" runtime_setup.py
if errorlevel 1 goto :install_error
start "" ".venv\Scripts\pythonw.exe" "%~dp0main.py"
exit /b 0
:no_python
echo Install 64-bit Python 3.11 or newer from https://www.python.org/downloads/windows/
echo Choose the option to add Python to PATH, then run this file again.
pause
exit /b 1
:install_error
echo Installation failed. Check your Internet connection and Python version.
pause
exit /b 1
