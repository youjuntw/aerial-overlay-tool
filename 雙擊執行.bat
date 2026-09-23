@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1

rem === Find Python: prefer the py launcher, skip the Microsoft Store alias ===
set "PYEXE="
py -3 --version >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE (
  for /f "delims=" %%p in ('where python 2^>nul') do (
    echo %%p | findstr /i "WindowsApps" >nul || if not defined PYEXE set "PYEXE=%%p"
  )
)

rem === Have Python -> run the .py source (Chinese filename via wildcard) ===
if defined PYEXE (
  echo Using Python: %PYEXE%
  for %%f in (*.py) do %PYEXE% "%%f"
  pause
  exit /b 0
)

rem === No Python -> launch the bundled .exe (no install needed) ===
for %%f in (*.exe) do (
  echo Python not found. Launching bundled app: %%f
  start "" "%%f"
  exit /b 0
)

rem === Neither Python nor .exe present ===
echo.
echo No Python and no .exe found in this folder.
echo   - Users:      double-click the app ^(.exe^) in this folder, no install needed.
echo   - Developers: install Python 3.10+ ^(check "Add python.exe to PATH"^),
echo                 then run:  pip install -r requirements.txt
echo.
echo If Python IS installed but you still see this, the Windows "App execution
echo aliases" python.exe stub is shadowing the real Python. Turn it off in
echo Settings ^> Apps ^> Advanced app settings ^> App execution aliases.
pause
exit /b 1
