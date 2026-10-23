@echo off
rem One-click portable build: engine -> desktop shell -> bundle -> zip.
rem Keep this file ASCII-only; the Chinese output comes from the PowerShell script.
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\build_portable_from_source.ps1" %*
set "EXIT_CODE=%errorlevel%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo Build stopped. Read the message above.
    pause
)
exit /b %EXIT_CODE%
