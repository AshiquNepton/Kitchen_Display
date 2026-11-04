
@echo off
rem Builds dist\KDS.exe (single file), then the installer (installer_output\KDS_Setup.exe) if Inno Setup is installed.
rem Run this on a 64-bit Windows PC with Python 3.10+ installed (needed only on the build PC).
cd /d "%~dp0"
python -m venv .venv || goto :err
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip || goto :err
pip install -r requirements.txt pyinstaller || goto :err
pyinstaller kds.spec --noconfirm --clean || goto :err
 
set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe
if exist "D:\Program Files\Inno Setup 7\ISCC.exe" set ISCC=D:\Program Files\Inno Setup 7\ISCC.exe
if exist "C:\Program Files\Inno Setup 7\ISCC.exe" set ISCC=C:\Program Files\Inno Setup 7\ISCC.exe
if exist "D:\Program Files (x86)\Inno Setup 7\ISCC.exe" set ISCC=D:\Program Files (x86)\Inno Setup 7\ISCC.exe
if defined ISCC ("%ISCC%" installer.iss || goto :err) else echo Inno Setup not found - open installer.iss in Inno Setup and press Compile.
echo.
echo Done.
exit /b 0
:err
echo BUILD FAILED
exit /b 1