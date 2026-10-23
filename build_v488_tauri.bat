@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\build_v484_local.ps1" -Version 4.88
exit /b %errorlevel%
