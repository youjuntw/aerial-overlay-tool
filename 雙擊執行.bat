@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set "PYEXE="

rem 1) 優先用官方 py 啟動器(最穩,直接避開 Microsoft Store 假捷徑)
py -3 --version >nul 2>nul && set "PYEXE=py -3"

rem 2) 沒有 py,就找 PATH 上「非 WindowsApps 假捷徑」的 python.exe
if not defined PYEXE (
  for /f "delims=" %%p in ('where python 2^>nul') do (
    echo %%p | findstr /i "WindowsApps" >nul || if not defined PYEXE set "PYEXE=%%p"
  )
)

if not defined PYEXE (
  echo.
  echo 找不到可用的 Python。
  echo 請到 https://www.python.org 安裝 Python 3.10 以上版本,
  echo 安裝時務必勾選「Add python.exe to PATH」。
  echo 安裝後,在本資料夾執行一次:pip install -r requirements.txt
  echo.
  echo (若你已裝 Python 卻仍看到此訊息:Windows「App execution aliases」的
  echo  python.exe 假捷徑蓋掉了真的 Python。到 設定 ^> 應用程式 ^> 進階應用程式設定
  echo  ^> 應用程式執行別名,把 python.exe / python3.exe 關掉即可。)
  pause
  exit /b 1
)

echo 使用 Python:%PYEXE%
for %%f in (*.py) do %PYEXE% "%%f"
pause
