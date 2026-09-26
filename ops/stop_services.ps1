# Stop the CRM backend / frontend started by ops\start_*.ps1.
#
# Needed before handing over to the scheduled tasks: the start scripts skip
# starting when the port is already in use, so the task would sit idle while the
# old manually-started server keeps the port.
#
# Usage:  powershell -ExecutionPolicy Bypass -File ops\stop_services.ps1
#
# NOTE: kept ASCII-only on purpose (PowerShell 5.1 reads .ps1 as GBK without a BOM).

$ErrorActionPreference = 'Continue'

function Stop-Port {
    param([int]$Port)

    $conns = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if (-not $conns) {
        Write-Host "[stop] port $Port is not in use"
        return
    }

    $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $pids) {
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if (-not $proc) { continue }

        # Only kill our own dev servers. Never touch unrelated processes that
        # happen to sit on the port (e.g. another project's node.exe).
        $isOurs =
            ($proc.ProcessName -eq 'python' -and $proc.Path -like '*\crm-sales-agent\backend\.venv\*') -or
            ($proc.ProcessName -eq 'node' -and $proc.Path -like '*\nodejs\*')
        if (-not $isOurs) {
            Write-Host "[stop] port $Port held by unrelated process $($proc.ProcessName) (pid $procId), left alone"
            continue
        }

        Write-Host "[stop] stopping $($proc.ProcessName) (pid $procId) on port $Port"
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
}

Stop-Port -Port 8000
Stop-Port -Port 5173

Start-Sleep -Seconds 2
Write-Host '[stop] done'
