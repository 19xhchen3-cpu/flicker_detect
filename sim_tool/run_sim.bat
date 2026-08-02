@echo off
setlocal
call conda activate flicker
if errorlevel 1 (
    echo Failed to activate conda environment: flicker
    exit /b 1
)
python "%~dp0my_flicker.py"
exit /b %errorlevel%
