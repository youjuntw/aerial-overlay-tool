@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1

rem === 找 Python:py 啟動器優先,並跳過 Microsoft Store 的 python.exe 假捷徑 ===
set "PYEXE="
py -3 --version >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE (
  for /f "delims=" %%p in ('where python 2^>nul') do (
    echo %%p | findstr /i "WindowsApps" >nul || if not defined PYEXE set "PYEXE=%%p"
  )
)

rem === 有 Python → 直接跑原始碼 ===
if defined PYEXE (
  echo 使用 Python:%PYEXE%
  for %%f in (*.py) do %PYEXE% "%%f"
  pause
  exit /b 0
)

rem === 沒 Python → 改開免安裝的 exe(給沒裝 Python 的同事)===
if exist "空拍套疊工具.exe" (
  echo 未偵測到 Python,改開免安裝版:空拍套疊工具.exe …
  start "" "空拍套疊工具.exe"
  exit /b 0
)

rem === 兩者都沒有 → 指引 ===
echo.
echo 這台電腦沒有 Python,本資料夾也沒有 空拍套疊工具.exe。
echo   - 若你是「使用者」:請直接雙擊本資料夾的「空拍套疊工具.exe」(免安裝,不需 Python)。
echo   - 若你是「開發者」:請到 https://www.python.org 安裝 Python 3.10+
echo     (安裝時勾選 Add python.exe to PATH),再執行 pip install -r requirements.txt。
echo.
echo (若已裝 Python 卻仍看到此訊息:Windows 的「App 執行別名」python.exe 假捷徑蓋掉了真的
echo  Python。到 設定 ^> 應用程式 ^> 進階應用程式設定 ^> 應用程式執行別名,關掉 python.exe。)
pause
exit /b 1
