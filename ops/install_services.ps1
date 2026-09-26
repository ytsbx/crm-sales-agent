# Register CRM backend + frontend as Windows scheduled tasks that start at logon.
#
# Why scheduled tasks: a dev server started by hand dies with the shell that
# launched it. A task runs under Task Scheduler, so the site keeps working after
# you close every terminal (and comes back after a reboot / re-logon).
#
# Usage (run from the repo root, in an elevated PowerShell if it complains):
#   powershell -ExecutionPolicy Bypass -File ops\install_services.ps1
#
# Remove them again:
#   Unregister-ScheduledTask -TaskName CRM-Backend -Confirm:$false
#   Unregister-ScheduledTask -TaskName CRM-Frontend -Confirm:$false
#
# NOTE: kept ASCII-only on purpose (PowerShell 5.1 reads .ps1 as GBK without a BOM).

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$backendScript = Join-Path $PSScriptRoot 'start_backend.ps1'
$frontendScript = Join-Path $PSScriptRoot 'start_frontend.ps1'

foreach ($s in @($backendScript, $frontendScript)) {
    if (-not (Test-Path $s)) {
        Write-Host "[install] missing script: $s"
        exit 1
    }
}

# cmd.exe is used because it is guaranteed to exist on every Windows install.
# '> nul 2>&1' swallows the server's own output; the real logs are written by
# the start_*.ps1 scripts into ops\logs.
function New-CrmTask {
    param([string]$Name, [string]$ScriptPath)

    $action = New-ScheduledTaskAction `
        -Execute 'cmd.exe' `
        -Argument ('/c powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}" > nul 2>&1' -f $ScriptPath) `
        -WorkingDirectory $root

    $trigger = New-ScheduledTaskTrigger -AtLogOn

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1)

    Register-ScheduledTask `
        -TaskName $Name `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description 'CRM Sales Agent local service (start at logon)' `
        -Force | Out-Null
}

Write-Host '[install] registering tasks...'
New-CrmTask -Name 'CRM-Backend' -ScriptPath $backendScript
New-CrmTask -Name 'CRM-Frontend' -ScriptPath $frontendScript

Write-Host '[install] starting them now...'
Start-ScheduledTask -TaskName 'CRM-Backend'
Start-ScheduledTask -TaskName 'CRM-Frontend'

Write-Host '[install] done. Waiting for the services to come up...'
$ok = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 2
    $api = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue
    $web = Get-NetTCPConnection -State Listen -LocalPort 5173 -ErrorAction SilentlyContinue
    if ($api -and $web) { $ok = $true; break }
}

if ($ok) {
    Write-Host '[install] backend  http://127.0.0.1:8000  OK'
    Write-Host '[install] frontend http://127.0.0.1:5173  OK'
} else {
    Write-Host '[install] services did not both come up. Check logs in ops\logs and run:'
    Write-Host '          Get-ScheduledTaskInfo -TaskName CRM-Backend'
    Write-Host '          Get-ScheduledTaskInfo -TaskName CRM-Frontend'
}

Write-Host ''
Write-Host '[install] task status:'
Get-ScheduledTask -TaskName 'CRM-*' |
    Select-Object TaskName, State |
    Format-Table -AutoSize |
    Out-String |
    Write-Host
