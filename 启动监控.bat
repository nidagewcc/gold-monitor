@echo off
chcp 65001 >nul
cd /d "%~dp0"
python gold_monitor.py
if errorlevel 1 (
  echo.
  echo 程序异常退出，请确认已安装 Python 及依赖（pip install -r requirements.txt）
  pause
)
