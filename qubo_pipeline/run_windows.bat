@echo off
setlocal

set "PYCMD="
where py >nul 2>nul
if not errorlevel 1 set "PYCMD=py"
if not defined PYCMD (
    where python >nul 2>nul
    if not errorlevel 1 set "PYCMD=python"
)
if not defined PYCMD (
    echo [ERROR] Neither 'py' nor 'python' was found on PATH.
    echo         Install Python from python.org and try again.
    pause
    exit /b 1
)

%PYCMD% "%~dp0run_pipeline.py" %*

if errorlevel 1 (
    echo.
    echo Pipeline exited with an error - see messages above.
)

echo.
echo Pipeline process finished. Press any key to close this window.
pause
