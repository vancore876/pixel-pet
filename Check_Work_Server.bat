@echo off
setlocal
cd /d "%~dp0"
if exist ".server-venv\Scripts\python.exe" (
    ".server-venv\Scripts\python.exe" tools\check_work_server.py
) else (
    py -3 tools\check_work_server.py
)
echo.
echo Keep this output when reporting a server problem. No accounts or notes were changed.
pause
