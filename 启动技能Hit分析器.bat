@echo off
chcp 65001 >nul
cd /d "%~dp0"
python run_hit_analyzer.py
if errorlevel 1 pause
