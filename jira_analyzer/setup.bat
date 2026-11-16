@echo off
rem ============================================================
rem  jira_analyzer setup: finds python 3.11+, creates .venv,
rem  installs deps, creates .env from template.
rem  ASCII ONLY. cmd.exe reads bat files in the OEM code page;
rem  any non-ASCII text (e.g. Cyrillic in UTF-8) breaks parsing
rem  and its fragments get executed as commands.
rem  All human-readable messages are printed by setup\start.py
rem  (it switches the console to UTF-8 itself).
rem ============================================================
setlocal EnableDelayedExpansion
set "PY="

rem 1) py-launcher (standard python.org installer)
call :probe py -3
if not errorlevel 1 set "PY=py -3"

rem 2) python in PATH
if not defined PY (
    call :probe python
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    call :probe python3
    if not errorlevel 1 set "PY=python3"
)

rem 3) standard install locations (python not in PATH; probe each by
rem    actually running it -- first in PATH may be wrong, e.g. Anaconda 3.9)
if not defined PY (
    for %%V in (Python314 Python313 Python312 Python311) do (
        if not defined PY (
            for %%B in ("%LOCALAPPDATA%\Programs\Python" "C:\Program Files" "C:") do (
                if not defined PY (
                    if exist "%%~B\%%V\python.exe" (
                        call :probe "%%~B\%%V\python.exe"
                        if not errorlevel 1 set "PY=%%~B\%%V\python.exe"
                    )
                )
            )
        )
    )
)

if not defined PY (
    echo [setup] No suitable Python 3.11+ found ^(py -3, python, python3 and
    echo standard install locations were all checked by actually running them^).
    echo Install Python 3.11+ from https://www.python.org/downloads/ and retry.
    pause
    exit /b 1
)

rem  Store the path WITHOUT quotes (a quoted path inside quotes would make
rem  cmd execute it as a command); quotes are added only at launch site.
set PY=%PY:"=%
echo [setup] Using: "%PY%"
"%PY%" "%~dp0setup\start.py" %*
set RC=%errorlevel%
echo.
echo You can close this window.
pause
exit /b %RC%

rem  Probe a command by actually running it. Args come via %*, NOT %1:
rem  for "py -3" %1 is only "py", and a bare py opens an interactive REPL.
:probe
%* -c "pass" >nul 2>&1
if errorlevel 1 exit /b 1
exit /b 0
