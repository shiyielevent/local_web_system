@echo off
setlocal EnableExtensions

REM ============================================================
REM Production one-click launcher for local_web_module_system.
REM All environment preparation is centralized in:
REM   backend\bootstrap_env.ps1
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
    -Mode System

set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] System startup failed. Exit code: %EXIT_CODE%
    pause
)

exit /b %EXIT_CODE%
