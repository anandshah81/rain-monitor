@echo off
echo ============================================
echo   Rain Monitor v3 - Refresh and Push to Web
echo ============================================
echo.

cd /d C:\Users\admin\claude\rain-monitor

echo Running rain_monitor.py (fetches IMD Pune)...
python rain_monitor.py
if errorlevel 1 (
  echo ERROR: rain_monitor.py failed. Not pushing.
  pause
  exit /b 1
)

echo.
echo Copying dashboard to repo...
python -c "import shutil; shutil.copy('rain_monsoon_monitor.html', 'index.html')"
if errorlevel 1 (
  echo ERROR: copy failed via python; falling back to powershell
  powershell -NoProfile -Command "Copy-Item -LiteralPath 'rain_monsoon_monitor.html' -Destination 'index.html' -Force"
)

REM Size sanity check — abort push if the copy produced a partial file
for %%A in (rain_monsoon_monitor.html) do set SRC_SIZE=%%~zA
for %%A in (index.html) do set DST_SIZE=%%~zA
if not "%SRC_SIZE%"=="%DST_SIZE%" (
  echo ERROR: index.html size %DST_SIZE% does not match source %SRC_SIZE%. Aborting.
  pause
  exit /b 1
)

echo Pushing to GitHub Pages...
git add index.html
git commit -m "Refresh dashboard %date% %time%"
git push

echo.
echo ============================================
echo   Done! Live at:
echo   https://anandshah81.github.io/rain-monitor/
echo.
echo   IMD updates weekly (Thursdays). Rerun on
echo   Thursday evenings for fresh data.
echo ============================================
pause
