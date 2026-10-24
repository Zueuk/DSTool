@echo off
REM Navigate to the directory containing this batch file
cd /d "%~dp0"

REM Detect and use venv if it exists, otherwise use system Python
if exist "venv\Scripts\python.exe" (
    call venv\Scripts\activate.bat
)

REM Adjust UI scaling if needed, this works for any Qt-based app
REM set QT_SCALE_FACTOR=1.33

REM Use pythonw.exe to run without the console window
python dstool_main.py %*

REM Keep window open on error
if errorlevel 1 pause