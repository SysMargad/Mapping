[CmdletBinding()]
param(
    [switch]$Publish,
    [string]$DriveShortcutRoot = "G:\.shortcut-targets-by-id",
    [string]$ArtsatMagArrowFolder = "G:\.shortcut-targets-by-id\1_OsDlKaTwrBt2WjvLpINfVET1O4yMTC2\MagArrow_TMI_RTP"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()
$repo = Split-Path -Parent $PSScriptRoot
$logRoot = Join-Path $env:LOCALAPPDATA "Mapping\logs"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logPath = Join-Path $logRoot "local-sync-$stamp.log"

function Resolve-Python {
    $runtime = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path -LiteralPath $runtime) {
        return $runtime
    }
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    throw "Python 3 was not found. Install Python 3 or the Codex runtime."
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Program,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )
    Write-Host "> $Program $($Arguments -join ' ')"
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed ($LASTEXITCODE): $Program"
    }
}

Start-Transcript -Path $logPath | Out-Null
try {
    $python = Resolve-Python
    $gpkg = Get-ChildItem -LiteralPath $ArtsatMagArrowFolder -Filter "*_MagArrow_TMI_RTP_points.gpkg" |
        Where-Object { -not $_.PSIsContainer } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $gpkg) {
        throw "MagArrow GPKG was not found: $ArtsatMagArrowFolder"
    }

    Push-Location $repo
    try {
        $safeRepo = $repo.Replace('\', '/')
        if ($Publish) {
            $dirty = @(git -C $repo -c "safe.directory=$safeRepo" status --porcelain)
            if ($dirty.Count -gt 0) {
                throw "The repository has uncommitted changes. Commit or stash them before publishing."
            }
            Invoke-Checked git -C $repo -c "safe.directory=$safeRepo" fetch origin main
            Invoke-Checked git -C $repo -c "safe.directory=$safeRepo" rebase origin/main
        }

        $trackerFiles = @(
            (Join-Path $DriveShortcutRoot "1XvKLs9QCgvIjHjQbXlV7thqPu0HdLIae\Hetsuu_Hutul_Drone_Project_Tracker.xlsx"),
            (Join-Path $DriveShortcutRoot "1dbk0R5iHgwgPfa8gT25wN-NhtOVLjQUZ\Artsat_Drone_Project_Tracker.xlsx"),
            (Join-Path $DriveShortcutRoot "1DwydImVmcQvj7s4loVRk_4K9-VQ7szXz\Buduunkhad_Drone_Project_Tracker.xlsx"),
            (Join-Path $DriveShortcutRoot "18zky2dhmLpBwOXWBLPFmHdT5QoqndiWk\Nergui_undur_Drone_Project_Tracker.xlsx")
        )
        foreach ($trackerFile in $trackerFiles) {
            if (-not (Test-Path -LiteralPath $trackerFile)) {
                throw "Tracker workbook is unavailable: $trackerFile"
            }
        }
        Invoke-Checked $python tools/export_project_trackers.py `
            @trackerFiles `
            --licences dist/data/context/licenses.geojson `
            --summary-output dist/data/context/project-trackers.json `
            --points-output dist/data/context/project-control-points.geojson
        Invoke-Checked $python tools/sync_local_drive_sources.py `
            --repo . `
            --drive-root $DriveShortcutRoot `
            --report-dir $logRoot
        Write-Host "Artsat MagArrow source: $($gpkg.FullName)"
        Invoke-Checked $python tools/import_magarrow_gpkg.py --gpkg $gpkg.FullName --repo . --project-key artsat
        Invoke-Checked $python tools/export_project_flight_coverage.py `
            dist/data/context/project-flight-tracks.geojson `
            --licences dist/data/context/licenses.geojson `
            --output dist/data/context/project-flight-coverage.json
        Invoke-Checked $python -m unittest discover -s tests -t .
        Invoke-Checked $python tools/validate_data.py

        & git -C $repo -c "safe.directory=$safeRepo" diff --quiet -- dist/data
        $dataChanged = $LASTEXITCODE -ne 0
        if (-not $dataChanged) {
            Write-Host "No new data. Nothing to publish."
            exit 0
        }

        $version = "$(Get-Date -Format 'yyyy-MM-dd').local.$stamp"
        Invoke-Checked $python tools/build_manifest.py --version $version --updated (Get-Date -Format "yyyy-MM-dd")
        Invoke-Checked $python tools/validate_data.py

        if (-not $Publish) {
            Write-Host "Local preview updated. Use -Publish to publish it."
            exit 0
        }

        Invoke-Checked git -C $repo -c "safe.directory=$safeRepo" add -- dist/data
        Invoke-Checked git -C $repo -c "safe.directory=$safeRepo" commit -m "Sync local Google Drive survey data"
        Invoke-Checked git -C $repo -c "safe.directory=$safeRepo" push origin HEAD:main
        Write-Host "Published successfully. GitHub Pages deployment will start automatically."
    }
    finally {
        Pop-Location
    }
}
catch {
    Write-Error $_
    exit 1
}
finally {
    Stop-Transcript | Out-Null
    Write-Host "Log: $logPath"
}
