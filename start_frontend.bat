@echo off
setlocal EnableExtensions

REM ============================================================
REM Frontend development/build launcher.
REM It does not prepare Python or install HTCondor.
REM ============================================================

set "PROJECT_ROOT=%~dp0"
set "BOOTSTRAP_PS1=%PROJECT_ROOT%backend\bootstrap_env.ps1"

if not exist "%BOOTSTRAP_PS1%" (
    echo [ERROR] Missing bootstrap script:
    echo         %BOOTSTRAP_PS1%
    pause
    exit /b 1
)

powershell.exe ^
    -NoLogo ^
    -NoProfile ^
    -ExecutionPolicy Bypass ^
    -File "%BOOTSTRAP_PS1%" ^
    -Mode Frontend

set "EXIT_CODE=%ERRORLEVEL%"

echo.
if "%EXIT_CODE%"=="0" (
    echo [OK] Frontend build completed.
) else (
    echo [ERROR] Frontend build failed. Exit code: %EXIT_CODE%
)

pause
exit /b %EXIT_CODE%
