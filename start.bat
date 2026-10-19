@echo off
setlocal
cd /d "%~dp0"
title Ledgerline

set "PY="
python --version >nul 2>nul && set "PY=python"
if not defined PY py --version >nul 2>nul && set "PY=py"
if not defined PY goto nopython

%PY% launch.py
if errorlevel 1 pause
exit /b

:nopython
echo.
echo  Python is not installed on this computer yet.
echo.
echo  1. Open  https://www.python.org/downloads/  and install Python.
echo  2. IMPORTANT: in the installer, tick "Add python.exe to PATH".
echo  3. Then double-click start.bat again.
echo.
pause
