@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Please run Start_Buddy.bat once before building.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements-build.txt
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --noconsole --onefile --name PixelSystemBuddy --add-data "assets;assets" main.py
if errorlevel 1 goto :failed
echo Done: dist\PixelSystemBuddy.exe
pause
exit /b 0
:failed
echo Build failed. Review the error above.
pause
exit /b 1
