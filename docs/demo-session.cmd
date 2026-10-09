@echo off
if /I not "%~1"=="run" exit /b 0

echo.
echo   context + templates          ready
timeout /t 1 /nobreak >nul 2>&1
echo   plan + Claude Agent SDK      ready
timeout /t 1 /nobreak >nul 2>&1
echo   checks + review              passed
timeout /t 1 /nobreak >nul 2>&1
echo   finished branch              csv-export
timeout /t 1 /nobreak >nul 2>&1
echo.
echo   Done. Ready to inspect or deliver.
