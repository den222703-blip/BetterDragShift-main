@echo off
rem DragShift launcher. Double-click this file to start the app.
rem   run.bat            start DragShift in the tray (no console window)
rem   run.bat console    start with a console window, to see error messages
rem   run.bat setup      only prepare the environment (build_exe.bat uses this)
setlocal EnableExtensions
cd /d "%~dp0"
title DragShift
set "MODE=%~1"
set "VENV=%~dp0.venv"
set "VPY=%VENV%\Scripts\python.exe"
set "VPYW=%VENV%\Scripts\pythonw.exe"

rem ---- 1. are we inside the DragShift source folder?
if not exist "run.py" goto :notsource
if not exist "dragshift\convert.py" goto :notsource

rem ---- 2. is there a working virtual environment? (a .venv copied from elsewhere does not work)
set "VENV_OK="
if exist "%VPY%" "%VPY%" -c "import sys" >nul 2>&1 && set "VENV_OK=1"
if exist "%VPY%" if not defined VENV_OK (
    echo The .venv folder is broken or was copied from another place. Recreating it...
    rmdir /s /q "%VENV%"
)
if not exist "%VPY%" call :makevenv
if not exist "%VPY%" goto :fail

rem ---- 3. are the libraries installed?
"%VPY%" -c "import importlib.util as u,sys;sys.exit(0 if all(u.find_spec(m) for m in ('PySide6','PIL','numpy','docx','pillow_heif','win32com')) and (u.find_spec('pymupdf') or u.find_spec('fitz')) else 1)" >nul 2>&1
if errorlevel 1 call :deps
if errorlevel 1 goto :fail

rem ---- 4. go
if /i "%MODE%"=="setup" goto :ok
if /i "%MODE%"=="console" goto :console

echo Starting DragShift. Look for its icon in the system tray, next to the clock.
echo If nothing appears, run "run.bat console" to see the error, or read
echo %LOCALAPPDATA%\DragShift\log.txt
start "" "%VPYW%" run.py
timeout /t 4 >nul 2>&1
goto :ok

:console
"%VPY%" run.py
echo.
echo DragShift has stopped.
pause
goto :ok

:ok
exit /b 0

:makevenv
set "PY="
for %%V in (3.12 3.13 3.11 3.10) do (
    if not defined PY (
        py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
    )
)
if not defined PY python -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY goto :nopython
echo Creating the virtual environment (.venv). This happens only once...
%PY% -m venv "%VENV%"
if errorlevel 1 exit /b 1
exit /b 0

:deps
echo Installing the libraries. This takes a few minutes and happens only once...
"%VPY%" -m pip install --upgrade pip >nul 2>&1
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
exit /b 0

:nopython
echo.
echo Python 3.10 to 3.13 was not found. Install Python 3.12 from python.org
echo and tick "Add python.exe to PATH" in the installer, then run this file again.
exit /b 1

:notsource
echo.
echo This folder does not contain the DragShift source code.
echo Download DragShift from GitHub first, then extract the zip INTO that folder,
echo so that run.py and the dragshift folder sit next to run.bat.
goto :fail

:fail
echo.
echo Something went wrong, see the messages above.
if /i not "%MODE%"=="setup" pause
exit /b 1
