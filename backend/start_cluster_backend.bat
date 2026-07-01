@echo off
setlocal EnableExtensions
cd /d "%~dp0"

REM Set this before running when the backend uses a specific Conda/venv Python:
REM set "LOCAL_WEB_PYTHON_EXE=D:\envs\rayenv\python.exe"

if defined LOCAL_WEB_PYTHON_EXE (
    set "PYTHON_EXE=%LOCAL_WEB_PYTHON_EXE%"
) else (
    set "PYTHON_EXE=python"
)

REM The platform controls process-level parallelism. Prevent BLAS/OpenMP oversubscription.
set "OPENBLAS_NUM_THREADS=1"
set "OMP_NUM_THREADS=1"
set "MKL_NUM_THREADS=1"
set "GOTO_NUM_THREADS=1"
set "NUMEXPR_NUM_THREADS=1"

echo [INFO] Python:
"%PYTHON_EXE%" -c "import sys; print(sys.executable); print(sys.version)"
if errorlevel 1 (
    echo [ERROR] Python is unavailable. Set LOCAL_WEB_PYTHON_EXE to the backend Python path.
    pause
    exit /b 1
)

echo [INFO] Starting the backend for LAN access: http://0.0.0.0:8000
"%PYTHON_EXE%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
