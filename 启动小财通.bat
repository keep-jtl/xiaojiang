@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   小财通（MVP 开发版）正在启动…
echo   启动后请勿关闭本窗口；关闭本窗口即退出软件
echo ============================================
".venv\Scripts\python.exe" -m app.main
pause
