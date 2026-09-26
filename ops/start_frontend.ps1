# Start the CRM frontend (Vite dev server). Safe to run repeatedly: skips if already up.
#
# Manual:  powershell -ExecutionPolicy Bypass -File ops\start_frontend.ps1
# Logs:    ops\logs\frontend.out.log / frontend.err.log
#
# NOTE: kept ASCII-only on purpose. Windows PowerShell 5.1 reads .ps1 as GBK
# unless the file has a UTF-8 BOM, which mangles non-ASCII source text.

# NOTE: must stay 'Continue' for the same reason as the backend script:
# tools that log to stderr must not be treated as terminating errors.
$ErrorActionPreference = 'Continue'

$root = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $root 'frontend'
$logDir = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$port = 5173

$busy = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host "[crm-frontend] port $port already listening, skip start"
    exit 0
}

if (-not (Test-Path (Join-Path $frontend 'node_modules'))) {
    Write-Host "[crm-frontend] node_modules missing, run 'npm install' inside frontend/ first"
    exit 1
}

$vite = Join-Path $frontend 'node_modules\.bin\vite.cmd'
if (-not (Test-Path $vite)) {
    Write-Host "[crm-frontend] vite not found: $vite"
    exit 1
}

Set-Location $frontend
$outLog = Join-Path $logDir 'frontend.out.log'
$errLog = Join-Path $logDir 'frontend.err.log'
Write-Host "[crm-frontend] starting on http://127.0.0.1:$port"
Write-Host "[crm-frontend] logs: $logDir\frontend.*.log"

# Supervisor loop: restart vite if it dies, so the site does not stay down.
while ($true) {
    & $vite --host 127.0.0.1 --port $port 1>> $outLog 2>> $errLog
    $code = $LASTEXITCODE
    Write-Host "[crm-frontend] exited (code $code), restarting in 5s"
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] vite exited with code $code, restarting" | Add-Content $errLog
    Start-Sleep -Seconds 5
}
