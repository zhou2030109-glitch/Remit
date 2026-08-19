@echo off
setlocal EnableExtensions
chcp 65001 >nul

rem Resolve paths from this file so double-clicking works from any directory.
set "ROOT=%~dp0"
set "LAUNCHER=%ROOT%tools\start_services.ps1"

if not exist "%LAUNCHER%" (
    echo [ERROR] Service launcher not found: %LAUNCHER%
    pause
    exit /b 1
)

if /i "%~1"=="--check" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" -Check
    exit /b %errorlevel%
)

set "MODE="
if /i "%~1"=="--visible" set "MODE=-Visible"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" %MODE%
if errorlevel 1 (
    echo.
    echo [ERROR] Startup failed. See the message above.
    pause
    exit /b 1
)

rem The launcher itself always closes; --visible only keeps the three service consoles.
endlocal
