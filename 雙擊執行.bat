@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1

rem 依序尋找可用的 Python:優先用 py 啟動器,再退回 PATH 的 python
set "PYEXE="
where py >nul 2>nul && set "PYEXE=py"
if not defined PYEXE ( where python >nul 2>nul && set "PYEXE=python" )
if not defined PYEXE (
  echo 找不到 Python。請先安裝 Python 3.10+ 並勾選「Add to PATH」,
  echo 然後執行:pip install -r requirements.txt
  pause
  exit /b 1
)

for %%f in (*.py) do %PYEXE% "%%f"
pause
