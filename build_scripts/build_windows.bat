@echo off
REM build_windows.bat — Build WFRP4e to TOW Converter for Windows
REM
REM Requirements (run once):
REM   pip install pyinstaller pillow
REM
REM Output:
REM   dist\WFRP4e_to_TOW_Converter.exe
REM

setlocal enabledelayedexpansion
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%\.."

echo =^> Cleaning previous build...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

echo =^> Running PyInstaller...
pyinstaller wfrp_tow_converter.spec --noconfirm

echo.
echo Build complete.
echo   Exe: dist\WFRP4e_to_TOW_Converter.exe
