@echo off
setlocal EnableExtensions
cd /d "%~dp0"
for %%F in (*.exe) do (
    if /I not "%%~nF"=="DragonNestEngine" (
        start "" "%%~fF"
        exit /b 0
    )
)
echo Desktop executable was not found next to this launcher.
pause
exit /b 1