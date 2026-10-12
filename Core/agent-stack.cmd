@echo off
setlocal
chcp 65001 >nul
echo %* | findstr /I /C:"-Hidden" >nul
if not errorlevel 1 (
    start "" powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0agent-stack.ps1" %*
    exit /b 0
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0agent-stack.ps1" %*
exit /b %ERRORLEVEL%