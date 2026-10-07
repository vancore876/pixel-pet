@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" runtime_setup.py
    if errorlevel 1 exit /b 1
    ".venv\Scripts\python.exe" main.py
) else (
    py -3 main.py
)
pause
