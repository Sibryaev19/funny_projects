@echo off
rem ============================================================
rem  Установка jira_analyzer: находит python, создаёт .venv,
rem  ставит зависимости, создаёт .env из шаблона.
rem  Двойной клик или запуск из консоли: setup.bat
rem ============================================================
setlocal
chcp 65001 >nul
set "PY="

rem 1) py-launcher (стандартный установщик python.org)
py -3 --version >nul 2>&1 && set "PY=py -3"

rem 2) python в PATH
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)

rem 3) стандартные пути установки (python не в PATH)
if not defined PY (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
        if exist "%%D\python.exe" set "PY=%%D\python.exe"
    )
)
if not defined PY (
    for /d %%D in ("C:\Python3*") do (
        if exist "%%D\python.exe" set "PY=%%D\python.exe"
    )
)

if not defined PY (
    echo [setup] Python не найден.
    echo Установи Python 3.11+ с https://www.python.org/downloads/
    echo ^(галочка "Add python.exe to PATH" не обязательна^) и запусти меня снова.
    pause
    exit /b 1
)

echo [setup] Использую: %PY%
%PY% "%~dp0setup\start.py" %*
echo.
echo Окно можно закрыть.
pause
