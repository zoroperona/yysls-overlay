@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto run
py -3.12 --version >nul 2>&1
if errorlevel 1 (
    echo Install Python 3.12 from https://www.python.org/downloads/windows/
    echo Include the Python Launcher and Tcl/Tk options, then try again.
    pause
    exit /b 1
)
py -3.12 -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
:run
.venv\Scripts\python.exe -c "import cv2, numpy, PIL, mss, tkinter" >nul 2>&1
if not errorlevel 1 goto launch
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
:launch
.venv\Scripts\python.exe buff_reminder_v2_dizi_hotkey.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo Startup failed. Please check the error above.
pause
exit /b 1
