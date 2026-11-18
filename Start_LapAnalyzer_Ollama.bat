@echo off
setlocal
cd /d "%~dp0"
echo === Lap Analyzer Pro ===
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3,11))" >nul 2>&1
  if not errorlevel 1 goto run
  echo Existing virtual environment is not usable. Rename .venv to .venv_old and try again.
  goto fail
)
py -3 -c "import sys; sys.exit(sys.version_info < (3,11))" >nul 2>&1
if not errorlevel 1 (
  py -3 -m venv .venv
  goto verify
)
python -c "import sys; sys.exit(sys.version_info < (3,11))" >nul 2>&1
if not errorlevel 1 (
  python -m venv .venv
  goto verify
)
for /d %%D in ("%LocalAppData%\Programs\Python\Python3*" "%ProgramFiles%\Python3*" "%SystemDrive%\Python3*" "%LocalAppData%\Python\pythoncore-3*") do (
  if exist "%%~fD\python.exe" (
    "%%~fD\python.exe" -c "import sys; sys.exit(sys.version_info < (3,11))" >nul 2>&1
    if not errorlevel 1 (
      "%%~fD\python.exe" -m venv .venv
      goto verify
    )
  )
)
echo Python 3.11+ was not found. Install it from https://www.python.org/downloads/windows/ and enable the Python launcher.
goto fail
:verify
if errorlevel 1 goto fail
if not exist ".venv\Scripts\python.exe" goto fail
:run
".venv\Scripts\python.exe" launch.py %*
if errorlevel 1 goto fail
exit /b 0
:fail
echo.
echo Startup failed. The error above explains the failed step.
pause
exit /b 1
