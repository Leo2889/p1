@echo off
rem ============================================================
rem  Launcher that does not depend on which python.exe comes
rem  first in PATH. It finds a working interpreter itself.
rem
rem  Examples:
rem    start.cmd
rem    start.cmd --app qt --duration 3600 --stress
rem    start.cmd --app wx
rem    start.cmd --compare
rem ============================================================
setlocal EnableExtensions
cd /d "%~dp0"

set "PY="

rem 1) project virtual environment
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"

rem 2) regular python.org installation
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PY if exist "%ProgramFiles%\Python312\python.exe" set "PY=%ProgramFiles%\Python312\python.exe"
if not defined PY if exist "%ProgramFiles%\Python311\python.exe" set "PY=%ProgramFiles%\Python311\python.exe"

rem 3) python from PATH, but only if it actually starts
if not defined PY (
  for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PY (
      "%%P" -c "import sys" >nul 2>&1 && set "PY=%%P"
    )
  )
)

rem 4) bundled Accio Work python (always present, but without tkinter)
if not defined PY if exist "%APPDATA%\Accio\pre-install\bd5ff2c6816a\python\python.exe" set "PY=%APPDATA%\Accio\pre-install\bd5ff2c6816a\python\python.exe"

if not defined PY (
  echo.
  echo Python not found. Install Python 3.12 from https://www.python.org/downloads/
  echo and run this script again.
  pause
  exit /b 1
)

echo [start] interpreter: %PY%
"%PY%" run.py %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [start] exit code %RC%
  pause
)
exit /b %RC%
