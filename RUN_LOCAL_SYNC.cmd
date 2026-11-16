@echo off
chcp 65001 >nul
echo Mapping local Google Drive sync эхэлж байна...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\run_local_sync.ps1" -Publish
set EXITCODE=%ERRORLEVEL%
echo.
if %EXITCODE% EQU 0 (
  echo Sync амжилттай дууслаа.
) else (
  echo Sync алдаатай дууслаа. Дээрх Log замыг шалгана уу.
)
pause
exit /b %EXITCODE%
