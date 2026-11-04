@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0.."
title img_translator — установка

echo === Установка окружения img_translator ===
echo.

set "PY="
set "N=0"

rem 1) python в PATH (пропускаем пустой стаб WindowsApps)
for /f "delims=" %%i in ('where python 2^>nul') do (
    if not defined PY (
        echo %%i | findstr /i "WindowsApps" >nul || set "PY=%%i"
    )
)

rem 2) лаунчер py (появляется при установке с python.org, даже без PATH)
if not defined PY (
    where py >nul 2>nul && set "PY=py -3"
)

rem 3) реестр: установки Python в любых папках (per-user и machine)
if not defined PY (
    for %%R in (HKCU HKLM) do (
        for /f "delims=" %%k in ('reg query "%%R\Software\Python\PythonCore" /k /reg:64 2^>nul ^| findstr /r "PythonCore\\3\."') do (
            for /f "tokens=2,*" %%a in ('reg query "%%k\InstallPath" /ve /reg:64 2^>nul') do (
                if exist "%%bpython.exe" (
                    set /a N+=1
                    set "CAND!N!=%%bpython.exe"
                )
            )
        )
    )
    if !N! equ 1 set "PY=!CAND1!"
    if !N! gtr 1 (
        echo Найдено несколько Python:
        for /l %%i in (1,1,!N!) do echo   %%i. !CAND%%i!
        set /p "SEL=Введите номер ^(1-!N!^): "
        for /f "delims=" %%i in ("!SEL!") do set "PY=!CAND%%i!"
    )
)

rem 4) вручную
if not defined PY (
    echo Python не найден автоматически.
    set /p "PY=Введите полный путь к python.exe ^(или Enter — отмена^): "
)

if not defined PY (echo Отмена. & pause & exit /b 1)
if not exist "%PY%" (echo Не найден файл: %PY% & pause & exit /b 1)

echo.
echo Интерпретатор: %PY%
"%PY%" --version || (echo Не запускается. & pause & exit /b 1)

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo Создаю .venv...
    "%PY%" -m venv .venv || (echo Ошибка создания venv. & pause & exit /b 1)
) else (
    echo .venv уже есть — пропускаю создание.
)

echo.
echo Ставлю зависимости...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo Ошибка pip. & pause & exit /b 1)

echo.
echo === Готово. Дальше: windows\preflight.bat ===
pause
