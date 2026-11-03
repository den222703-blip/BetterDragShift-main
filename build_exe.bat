@echo off
rem Builds the DragShift program (an .exe) from the source code.
rem   build_exe.bat              folder build: dist\DragShift\DragShift.exe (for the installer)
rem   build_exe.bat onefile      one portable file: dist\DragShift.exe
rem   build_exe.bat player       also include the video player of the Trim window (bigger build)
rem Options can be combined, e.g. build_exe.bat onefile player
rem Put ffmpeg.exe, ffprobe.exe and the realesrgan folder into bin\ first if the build should contain them.
setlocal EnableExtensions
cd /d "%~dp0"
title DragShift - build exe

set "OPT_ONEFILE="
set "OPT_PLAYER="
:args
if "%~1"=="" goto :argsdone
if /i "%~1"=="onefile" set "OPT_ONEFILE=1"
if /i "%~1"=="player" set "OPT_PLAYER=1"
shift
goto :args
:argsdone

rem ---- environment and libraries (run.bat does the work)
call "%~dp0run.bat" setup
if errorlevel 1 goto :fail
set "VPY=%~dp0.venv\Scripts\python.exe"

rem ---- PyInstaller
"%VPY%" -c "import importlib.util as u,sys;sys.exit(0 if u.find_spec('PyInstaller') else 1)" >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    "%VPY%" -m pip install "pyinstaller>=6.6"
    if errorlevel 1 goto :fail
)

rem ---- a running copy would keep its files locked
taskkill /f /im DragShift.exe >nul 2>&1

set "ONEFILE="
set "PLAYER="
if defined OPT_ONEFILE set "ONEFILE=1"
if defined OPT_PLAYER set "PLAYER=1"

echo.
if exist "bin\ffmpeg.exe" echo [included] bin\ffmpeg.exe
if not exist "bin\ffmpeg.exe" echo [missing ] bin\ffmpeg.exe - video tools will look for ffmpeg on PATH instead
if exist "bin\realesrgan\realesrgan-ncnn-vulkan.exe" echo [included] bin\realesrgan - AI upscale
if not exist "bin\realesrgan\realesrgan-ncnn-vulkan.exe" echo [missing ] bin\realesrgan - AI upscale will offer only the Fast mode
if defined OPT_PLAYER echo [option  ] video player included
if defined OPT_ONEFILE echo [option  ] one portable file
echo.
echo Building. This takes a few minutes...
"%VPY%" -m PyInstaller --noconfirm --clean DragShift.spec
if errorlevel 1 goto :fail

set "OUT=dist\DragShift\DragShift.exe"
if defined OPT_ONEFILE set "OUT=dist\DragShift.exe"
if not exist "%OUT%" goto :fail

echo.
echo Self-test of the built program...
start "" /wait "%OUT%" --selftest
set "RC=%errorlevel%"
if not "%RC%"=="0" goto :smokefail

echo.
echo ============================================================
echo  DONE. Your program: %OUT%
echo  Self-test passed.
echo ============================================================
start "" explorer "%~dp0dist"
if /i not "%NOPAUSE%"=="1" pause
exit /b 0

:smokefail
echo.
echo The program was built, but its self-test failed with code %RC%.
echo Details: %LOCALAPPDATA%\DragShift\log.txt
goto :fail

:fail
echo.
echo The build did not finish, see the messages above.
if /i not "%NOPAUSE%"=="1" pause
exit /b 1
