@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0.."
title img_translator — проверка машины
set "PY=%CD%\.venv\Scripts\python.exe"
if not exist "%PY%" (echo Не найден .venv — сначала запустите windows\install.bat & pause & exit /b 1)
"%PY%" windows\launch.py preflight
pause
