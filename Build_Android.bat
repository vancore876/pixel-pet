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
echo Building an installation APK with your Expo account.
echo Read mobile\README.md for signing, identifiers, and first-time setup.
call npx eas-cli@latest build --platform android --profile preview
if errorlevel 1 goto :failed
pause
exit /b 0
:failed
echo The build did not finish. Review the message above and mobile\README.md.
pause
exit /b 1
