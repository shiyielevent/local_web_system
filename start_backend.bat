@echo off
setlocal EnableExtensions

REM ============================================================
REM Backend development launcher.
REM It uses the same bootstrap logic as start_system.bat, but
REM skips the frontend build.
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
    -Mode Backend

set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] Backend startup failed. Exit code: %EXIT_CODE%
    pause
)

exit /b %EXIT_CODE%
