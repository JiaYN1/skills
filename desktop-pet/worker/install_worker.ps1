$ErrorActionPreference = "Stop"

$WorkerRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $WorkerRoot

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "找不到 Python Launcher。请先安装 Python 3.11+，并勾选 Add Python to PATH。"
}

py -3.11 -m venv .venv
& "$WorkerRoot\.venv\Scripts\python.exe" -m pip install --upgrade pip
& "$WorkerRoot\.venv\Scripts\python.exe" -m pip install -r requirements.txt

if (-not (Test-Path "$WorkerRoot\.env")) {
    Copy-Item "$WorkerRoot\.env.example" "$WorkerRoot\.env"
    Write-Host "已创建 worker/.env，请填写 PET_SERVER_URL 和 WORKER_TOKEN。" -ForegroundColor Yellow
} else {
    Write-Host "worker/.env 已存在，未覆盖。" -ForegroundColor Green
}

Write-Host "安装完成。下一步运行 .\check_connection.ps1。" -ForegroundColor Green

