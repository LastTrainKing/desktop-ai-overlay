@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在检查依赖...
python -m pip install --quiet --disable-pip-version-check PySide6 uiautomation pywin32
if errorlevel 1 (
  echo 依赖安装失败，请确认已安装 Python 3.10+ 并加入 PATH
  pause
  exit /b 1
)
echo 启动桌面悬浮助手...
start "" pythonw run.py
exit /b 0
