@echo off
setlocal
rem Absolute paths let this launcher work from UNC shares and mapped drives.
set "JEFFERY_WORK_BASE=%LOCALAPPDATA%\PixelSystemBuddy"
if not defined LOCALAPPDATA set "JEFFERY_WORK_BASE=%USERPROFILE%\AppData\Local\PixelSystemBuddy"
set "JEFFERY_WORK_RUNTIME=%JEFFERY_WORK_BASE%\work-runtime"
set "PYTHONDONTWRITEBYTECODE=1"
if not exist "%JEFFERY_WORK_RUNTIME%\Scripts\python.exe" (
    echo First run: installing Jeffery for your Windows account...
    py -3 -m venv "%JEFFERY_WORK_RUNTIME%"
    if errorlevel 1 (
        python -m venv "%JEFFERY_WORK_RUNTIME%"
        if errorlevel 1 goto :no_python
    )
)
"%JEFFERY_WORK_RUNTIME%\Scripts\python.exe" -c "import struct, sys; raise SystemExit(0 if sys.version_info >= (3, 11) and struct.calcsize('P') == 8 else 1)"
if errorlevel 1 goto :no_python
"%JEFFERY_WORK_RUNTIME%\Scripts\python.exe" "%~dp0runtime_setup.py"
if errorlevel 1 goto :install_error
start "" /D "%JEFFERY_WORK_BASE%" "%JEFFERY_WORK_RUNTIME%\Scripts\pythonw.exe" "%~dp0work_buddy.py"
if errorlevel 1 goto :launch_error
exit /b 0
:no_python
echo Install 64-bit Python 3.11 or newer from https://www.python.org/downloads/windows/
echo Choose the option to add Python to PATH, then run this file again.
pause
exit /b 1
:install_error
echo Installation failed. Check your Internet connection and access to the shared files.
pause
exit /b 1
:launch_error
echo Jeffery could not start. Check access to the shared application folder.
pause
exit /b 1
