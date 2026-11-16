[CmdletBinding()]
param(
    [string]$TaskName = "Mapping Local Drive Sync",
    [datetime]$DailyAt = "08:00"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$repo = Split-Path -Parent $PSScriptRoot
$syncScript = Join-Path $PSScriptRoot "run_local_sync.ps1"

if (-not (Test-Path -LiteralPath $syncScript)) {
    throw "Sync script олдсонгүй: $syncScript"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$syncScript`" -Publish" `
    -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Google Drive Desktop-оос Mapping trajectory-г шалгаж GitHub Pages-д нийтэлнэ." `
    -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "Task суулгалаа: $($task.TaskName)"
Write-Host "Дараагийн ажиллах цаг: $($info.NextRunTime)"
