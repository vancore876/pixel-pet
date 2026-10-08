@echo off
setlocal
cd /d "%~dp0"
if not exist ".server-venv\Scripts\python.exe" (
    echo Set up the optional Python work server first. See WORK_CHAT.md.
    pause
    exit /b 1
)
if not exist "server-data\tls\server.crt" goto :missing_tls
if not exist "server-data\tls\server.key" goto :missing_tls
".server-venv\Scripts\python.exe" -m work_server --host 0.0.0.0 --port 8443 --database "server-data\work-chat.sqlite" --certfile "server-data\tls\server.crt" --keyfile "server-data\tls\server.key"
exit /b %errorlevel%
:missing_tls
echo A trusted server certificate and private key are required for office access.
echo Follow WORK_CHAT.md and save them in server-data\tls before starting.
pause
exit /b 1
