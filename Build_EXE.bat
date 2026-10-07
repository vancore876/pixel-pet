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
set "SEMANTIC_ARGS=--exclude-module sentence_transformers --exclude-module torch --exclude-module transformers"
if /i "%~1"=="semantic" (
    echo Installing optional CPU-only semantic recall dependencies...
    ".venv\Scripts\python.exe" -m pip install "torch==2.14.1" --index-url https://download.pytorch.org/whl/cpu
    if errorlevel 1 goto :failed
    ".venv\Scripts\python.exe" -m pip install -r requirements-semantic.txt
    if errorlevel 1 goto :failed
    set "SEMANTIC_ARGS=--collect-all sentence_transformers --collect-all transformers --collect-all tokenizers --collect-all safetensors --copy-metadata torch --copy-metadata sentence-transformers --copy-metadata huggingface-hub"
)
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --noconsole --onefile --name PixelSystemBuddy --add-data "assets;assets" --add-data "THIRD_PARTY_NOTICES.md;." --collect-all pypdfium2 --collect-all pypdfium2_raw --copy-metadata pypdfium2 --collect-data trafilatura --collect-data justext --collect-all rapidfuzz --copy-metadata RapidFuzz --copy-metadata Pillow --copy-metadata trafilatura %SEMANTIC_ARGS% main.py
if errorlevel 1 goto :failed
echo Done: dist\PixelSystemBuddy.exe
pause
exit /b 0
:failed
echo Build failed. Review the error above.
pause
exit /b 1
