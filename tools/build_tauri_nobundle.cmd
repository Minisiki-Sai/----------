@echo off
setlocal EnableExtensions
rem Build the desktop shell without an installer bundle.
rem The MSVC/SDK environment is assembled explicitly instead of VsDevCmd.bat,
rem because that script shells out to reg.exe which this sandbox blocks.
set "MSVC_ROOT=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC\14.44.35207"
set "SDK_ROOT=C:\Program Files (x86)\Windows Kits\10"
set "SDK_VER=10.0.26100.0"
if not exist "%MSVC_ROOT%\bin\Hostx64\x64\link.exe" (
    echo [portable] MSVC link.exe not found under "%MSVC_ROOT%"
    exit /b 1
)

set "PATH=%USERPROFILE%\.cargo\bin;%MSVC_ROOT%\bin\Hostx64\x64;%SDK_ROOT%\bin\%SDK_VER%\x64;%PATH%"
set "LIB=%MSVC_ROOT%\lib\x64;%SDK_ROOT%\Lib\%SDK_VER%\ucrt\x64;%SDK_ROOT%\Lib\%SDK_VER%\um\x64;%LIB%"
set "INCLUDE=%MSVC_ROOT%\include;%SDK_ROOT%\Include\%SDK_VER%\ucrt;%SDK_ROOT%\Include\%SDK_VER%\um;%SDK_ROOT%\Include\%SDK_VER%\shared;%SDK_ROOT%\Include\%SDK_VER%\winrt;%INCLUDE%"
set "CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER=%MSVC_ROOT%\bin\Hostx64\x64\link.exe"

rem pnpm is not always on the Windows PATH; reuse the same discovery as the dev launcher.
set "PNPM="
for %%p in (pnpm.cmd) do if not defined PNPM set "PNPM=%%~$PATH:p"
if not defined PNPM if exist "%APPDATA%\npm\pnpm.cmd" set "PNPM=%APPDATA%\npm\pnpm.cmd"
if not defined PNPM if exist "%ProgramFiles%\nodejs\pnpm.cmd" set "PNPM=%ProgramFiles%\nodejs\pnpm.cmd"
if not defined PNPM for /d %%d in ("%USERPROFILE%\.workbuddy\binaries\node\versions\*") do if exist "%%d\pnpm.CMD" set "PNPM=%%d\pnpm.CMD"
if not defined PNPM (
    echo [portable] pnpm not found. Install Node.js and run: npm i -g pnpm
    exit /b 1
)
for %%d in ("%PNPM%") do set "PATH=%%~dpd;%PATH%"

set "FRONTEND=%~dp0..\frontend"
if exist "%FRONTEND%\dist" rmdir /s /q "%FRONTEND%\dist"

cd /d "%FRONTEND%"
call "%PNPM%" build:web
if errorlevel 1 exit /b %errorlevel%

call "%PNPM%" exec tauri build --no-bundle --config "%~dp0tauri.portable.conf.json"
exit /b %errorlevel%
