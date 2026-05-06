@echo off
setlocal

cd /d "%~dp0"

echo ========================================
echo 合同管理系统 - 依赖安装
echo ========================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo 未找到虚拟环境，正在创建 .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo 创建虚拟环境失败，请确认 Python 已正确安装并加入 PATH。
        pause
        exit /b 1
    )
)

echo 正在升级 pip ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo.
    echo pip 升级失败。
    pause
    exit /b 1
)

echo.
echo 正在安装 requirements.txt 中的依赖 ...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo 依赖安装失败，请检查网络或 requirements.txt。
    pause
    exit /b 1
)

echo.
echo 依赖安装完成。
echo 之后可运行：.venv\Scripts\python.exe main.py
echo.
pause
