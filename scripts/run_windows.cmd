@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_windows.ps1" %*
exit /b %errorlevel%
