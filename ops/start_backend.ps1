# Start the CRM backend (uvicorn). Safe to run repeatedly: skips if already up.
#
# Manual:  powershell -ExecutionPolicy Bypass -File ops\start_backend.ps1
# Logs:    ops\logs\backend.out.log / backend.err.log
#
# NOTE: kept ASCII-only on purpose. Windows PowerShell 5.1 reads .ps1 as GBK
# unless the file has a UTF-8 BOM, which mangles non-ASCII source text.

# NOTE: must stay 'Continue'. uvicorn writes its INFO logs to stderr, and with
# 'Stop' PowerShell turns the first log line into a terminating error and kills
# the server right after it boots (cost me one confusing debugging round).
$ErrorActionPreference = 'Continue'

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
$logDir = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$port = 8000

$busy = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host "[crm-backend] port $port already listening, skip start"
    exit 0
}

if (-not (Test-Path $python)) {
    Write-Host "[crm-backend] venv not found: $python"
    Write-Host "[crm-backend] run first, inside backend/:"
    Write-Host "  uv venv --python 3.12 .venv"
    Write-Host "  uv pip install --python .venv\Scripts\python.exe -r requirements.txt"
    exit 1
}

$pg = Get-NetTCPConnection -State Listen -LocalPort 5433 -ErrorAction SilentlyContinue
if (-not $pg) {
    Write-Host "[crm-backend] WARNING: 5433 (PostgreSQL) is not listening. Start middleware first:"
    Write-Host "  docker compose -f ops\docker-compose.yml up -d"
}

Set-Location $backend
$outLog = Join-Path $logDir 'backend.out.log'
$errLog = Join-Path $logDir 'backend.err.log'
Write-Host "[crm-backend] starting on http://127.0.0.1:$port"
Write-Host "[crm-backend] logs: $logDir\backend.*.log"

# Supervisor loop: if uvicorn dies (crash, port stolen, OOM), restart it after
# a short pause instead of leaving the site down until someone notices.
while ($true) {
    & $python -m uvicorn app.main:app --host 127.0.0.1 --port $port 1>> $outLog 2>> $errLog
    $code = $LASTEXITCODE
    Write-Host "[crm-backend] exited (code $code), restarting in 5s"
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] uvicorn exited with code $code, restarting" | Add-Content $errLog
    Start-Sleep -Seconds 5
}
