@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "ROOT=%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%tools\stop_services.ps1"
if errorlevel 1 (
    echo.
    echo [ERROR] Some services could not be stopped.
    pause
    exit /b 1
)

timeout /t 2 /nobreak >nul
endlocal
