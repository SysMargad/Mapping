@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install_local_sync_task.ps1"
set EXITCODE=%ERRORLEVEL%
echo.
if %EXITCODE% EQU 0 (
  echo Өдөр тутмын локал sync task амжилттай суулгалаа.
) else (
  echo Task суулгахад алдаа гарлаа.
)
pause
exit /b %EXITCODE%
