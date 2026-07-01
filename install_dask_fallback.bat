@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if defined LOCAL_WEB_PYTHON_EXE (
    set "PYTHON_EXE=%LOCAL_WEB_PYTHON_EXE%"
) else (
    set "PYTHON_EXE=python"
)

"%PYTHON_EXE%" -m pip install --disable-pip-version-check -r requirements_dask.txt
if errorlevel 1 (
    echo [ERROR] Dask dependency installation failed.
    pause
    exit /b 1
)

echo [OK] Dask Distributed dependencies installed.
pause
