@echo off
setlocal EnableExtensions
set "PNPM=pnpm"
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd" set "PNPM=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd"
call "%PNPM%" --package=@cloudbase/cli dlx tcb %*
exit /b %errorlevel%
