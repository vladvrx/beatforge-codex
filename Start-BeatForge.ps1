param(
    [switch]$Setup,
    [switch]$Check,
    [ValidateRange(1024, 65535)][int]$Port = 8001
)
$ErrorActionPreference = 'Stop'
$studioRoot = $PSScriptRoot
$studioPython = Join-Path $studioRoot '.venv\Scripts\python.exe'
Push-Location -LiteralPath $studioRoot
try {
    if ($Setup) {
        if (-not (Test-Path -LiteralPath $studioPython)) {
            python -m venv .venv
            if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11 or newer, then retry -Setup.' }
        }
        & $studioPython -m pip install -e '.[dev,studio]'
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check the pip output above.' }
    }
    if (-not (Test-Path -LiteralPath $studioPython)) {
        throw 'The project environment is missing. Run .\Start-BeatForge.ps1 -Setup first.'
    }
    & $studioPython tools\doctor.py
    if ($LASTEXITCODE -ne 0) { throw 'Fix the required dependency checks above before starting Studio.' }
    if ($Check) { return }
    Write-Host "Open http://127.0.0.1:$Port/ to use BeatForge Studio. Press Ctrl+C to stop."
    & $studioPython -m uvicorn beatforge.api:app --host 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'Studio stopped with an error. Check the server output above.' }
} finally {
    Pop-Location
}
