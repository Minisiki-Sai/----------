@echo off
chcp 65001 >nul
echo Installing Microsoft Visual C++ Build Tools for Tauri...
echo A Windows administrator confirmation will appear once.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Start-Process winget -Verb RunAs -Wait -ArgumentList 'install --id Microsoft.VisualStudio.2022.BuildTools --exact --accept-package-agreements --accept-source-agreements --override ""--wait --passive --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended""'"
if errorlevel 1 (
  echo.
  echo Installation failed. Check the Visual Studio Installer log.
) else (
  echo.
  echo Build Tools installation finished.
)
pause
