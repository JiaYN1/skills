$ErrorActionPreference = "Stop"

$WorkerRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $WorkerRoot ".env"
$Python = Join-Path $WorkerRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $EnvFile)) {
    throw "找不到 worker/.env。请先运行 .\install_worker.ps1。"
}
if (-not (Test-Path $Python)) {
    throw "找不到虚拟环境。请先运行 .\install_worker.ps1。"
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

