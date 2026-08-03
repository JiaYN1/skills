$ErrorActionPreference = "Stop"

$WorkerRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $WorkerRoot ".env"
$Python = Join-Path $WorkerRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $EnvFile)) {
    throw "worker/.env was not found. Run .\install_worker.ps1 first."
}
if (-not (Test-Path $Python)) {
    throw "Worker virtual environment was not found. Run .\install_worker.ps1 first."
}

Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $parts = $line.Split("=", 2)
        [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
    }
}

& $Python (Join-Path $WorkerRoot "worker.py")
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
