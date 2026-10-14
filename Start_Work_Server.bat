@echo off
setlocal
cd /d "%~dp0"
call "%~dp0Setup_Work_Server.bat"
if errorlevel 1 exit /b 1
set "JEFFERY_WORK_SERVER_DATA=%LOCALAPPDATA%\PixelSystemBuddy\work-server"
if not defined LOCALAPPDATA set "JEFFERY_WORK_SERVER_DATA=%USERPROFILE%\AppData\Local\PixelSystemBuddy\work-server"
echo Starting Jeffery Work Chat on office network port 8765.
echo Enter this computer's IP in Jeffery on each work PC.
echo Leave this window open. Press Ctrl+C to stop chat.
echo Accounts and messages are stored privately in "%JEFFERY_WORK_SERVER_DATA%".
".server-venv\Scripts\python.exe" -m work_server --host 0.0.0.0 --port 8765 --database "%JEFFERY_WORK_SERVER_DATA%\work-chat.sqlite" --allow-lan-http
if errorlevel 1 (
    echo The work server stopped with an error. See the message above and WORK_CHAT.md.
    pause
    exit /b 1
)
exit /b 0
