@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
if not exist "%ROOT%runtime\python\python.exe" (
    echo [ERROR] 未找到打包运行时，请重新安装 Remit。
    pause
    exit /b 1
)
"%ROOT%runtime\python\python.exe" -B "%ROOT%tools\remit_prod_app.py" --stop
endlocal
