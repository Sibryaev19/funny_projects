@echo off
rem ============================================================
rem  jira_analyzer web UI launcher (double click).
rem  Environment must be installed first -- see setup.bat
rem  ASCII ONLY: cmd.exe reads bat files in the OEM code page,
rem  Cyrillic in UTF-8 breaks parsing. Keep this file ASCII.
rem ============================================================
setlocal
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo [start] Environment is not installed.
    echo Run setup.bat first - it creates .venv and installs dependencies.
    pause
    exit /b 1
)
echo ============================================================
echo   jira_analyzer - web UI
echo.
echo   Open in browser:  http://127.0.0.1:8765
echo   (change port in .env: WEB_PORT=...)
echo.
echo   Stop: close this window or press Ctrl+C
echo ============================================================
"%~dp0.venv\Scripts\python.exe" -m src.web.app %*
echo.
echo Server stopped. You can close this window.
pause
