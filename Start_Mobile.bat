@echo off
setlocal
cd /d "%~dp0mobile"
where node >nul 2>nul
if errorlevel 1 (
    echo Install Node.js 24 LTS from https://nodejs.org/ then try again.
    pause
    exit /b 1
)
if not exist "node_modules\expo\package.json" (
    call npm ci
    if errorlevel 1 goto :failed
)
echo Open mobile\README.md for phone builds and notification setup.
call npm start
exit /b %errorlevel%
:failed
echo Installation failed. Check your internet connection and Node.js version.
pause
exit /b 1
