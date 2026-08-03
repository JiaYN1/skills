$ErrorActionPreference = "Stop"

$WorkerRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $WorkerRoot

if (Get-Command py -ErrorAction SilentlyContinue) {
    py -3.11 -m venv .venv
} else {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $PythonCommand) {
        throw "Python 3.11+ was not found. Install Python and add it to PATH."
    }
    $BasePython = $PythonCommand.Source
    $PythonVersion = & $BasePython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ([version]$PythonVersion -lt [version]"3.11") {
        throw "Python 3.11+ is required; found $PythonVersion."
    }
    Write-Host "Python Launcher was not found; using $BasePython" -ForegroundColor Yellow
    & $BasePython -m venv .venv
}

$Python = Join-Path $WorkerRoot ".venv\Scripts\python.exe"
$PreviousPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$null = & $Python -c "from tkinter import Tcl; Tcl()" 2>$null
$TkExitCode = $LASTEXITCODE
$ErrorActionPreference = $PreviousPreference
if ($TkExitCode -ne 0) {
    throw "Tkinter is unavailable or incomplete. Install a Python 3.11+ distribution with Tcl/Tk support before building desktop-pet executables."
}
& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements.txt

if (-not (Test-Path "$WorkerRoot\.env")) {
    Copy-Item "$WorkerRoot\.env.example" "$WorkerRoot\.env"
    Write-Host "Created worker/.env. Set PET_SERVER_URL and WORKER_TOKEN before starting the worker." -ForegroundColor Yellow
} else {
    Write-Host "worker/.env already exists; it was not overwritten." -ForegroundColor Green
}

Write-Host "Installation complete. Next run .\check_connection.ps1." -ForegroundColor Green
