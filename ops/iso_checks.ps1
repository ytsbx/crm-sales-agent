# 隔离回归跑法（Windows / 本机 Docker 中间件）
#
# 为什么需要它：`ops/run_checks.sh` 默认打 127.0.0.1:8000，也就是**开发库**。
# 24 号交接说明 §1 明确要求「不能用跑完删测试数据代替事前隔离」。这个脚本把
# 那套流程固定下来：一次性可丢弃库 + 独立后端实例 + 显式断开对外通道。
#
# 它会做四件事：
#   1. 建/重建一次性库（库名必须以 crm_iso 或 crm_check 开头，防止误指开发库）
#   2. 在**该库**上跑 alembic upgrade head
#   3. 起一个独立后端（独立端口、独立 FILE_ROOT、钉钉/企微推送总闸保持关闭）
#   4. 按 ops/check_suites.txt 顺序跑接口回归
#
# 用法（在仓库根目录）：
#   pwsh ops/iso_checks.ps1 -Db crm_iso_a -Port 8011
#   pwsh ops/iso_checks.ps1 -Db crm_iso_a -Port 8011 -Suites check_quote_api,check_data_scope
#   pwsh ops/iso_checks.ps1 -Db crm_iso_a -Port 8011 -KeepDb -KeepServer   # 手工排查用
#
# 注意：脚本**不会**碰 crm_sales_agent（开发库）。库名不合法就直接退出。

param(
    [string]$Db = "crm_iso",
    [int]$Port = 8011,
    [string[]]$Suites = @(),
    [switch]$KeepDb,
    [switch]$KeepServer
)

# 注意：这里**不能**设成 Stop。docker / psql 会把 NOTICE 写到 stderr，
# PowerShell 5.1 把原生命令的 stderr 当错误记录，一设 Stop 就会在
# 「库不存在，跳过删除」这种正常提示上直接中断脚本。失败靠 $LASTEXITCODE 判。
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$pgContainer = "crm-postgres"
$pgUser = "crm"
$logDir = Join-Path $root "ops/logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if ($Db -notmatch '^crm_(iso|check)') {
    throw "拒绝执行：库名 '$Db' 不是一次性隔离库（必须以 crm_iso 或 crm_check 开头）"
}

# 很多既有套件自己有一道护栏：库名里必须含 "test"（它们会写数据，怕误指开发库）。
# 这里做一次归一，而不是让每个调用方去记这个约定：`crm_iso_a` → `crm_iso_test_a`。
# 库名只是标识，语义（一次性、可丢弃）没有变，所以自动补齐比报错更不容易漏跑。
if ($Db -notmatch 'test') {
    $suffix = $Db -replace '^crm_(iso|check)_?', ''
    if (-not $suffix) { $suffix = 'run' }
    $Db = "crm_iso_test_$suffix"
    Write-Host "（库名不含 test，已按既有套件的护栏改写为 $Db）"
}

$dbUrl = "postgresql+asyncpg://${pgUser}:crm123456@127.0.0.1:5433/$Db"

Write-Host "== 1. 重建一次性库 $Db =="
docker exec $pgContainer psql -U $pgUser -d postgres -c "DROP DATABASE IF EXISTS $Db;" 2>$null | Out-Null
docker exec $pgContainer psql -U $pgUser -d postgres -c "CREATE DATABASE $Db;" 2>$null | Out-Null

Write-Host "== 2. 迁移（只在 $Db 上）=="
Push-Location $backend
$env:DATABASE_URL = $dbUrl
$env:PYTHONPATH = "."
$env:PYTHONDONTWRITEBYTECODE = "1"
& .\.venv\Scripts\python.exe -m alembic upgrade head 2>&1 | Select-Object -Last 3
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "alembic upgrade head 失败" }

# 种子账号（admin / zhangsan / lisi / wangwu）：接口回归脚本一律用它们登录，
# 空库不跑这一步会在 login 处整体失败。脚本幂等，重复执行只跳过已存在的。
& .\.venv\Scripts\python.exe -m scripts.seed 2>&1 | Select-Object -Last 2
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "scripts.seed 失败" }

Write-Host "== 3. 起独立后端 127.0.0.1:$Port（对外通道全关）=="
$env:DINGTALK_PUSH_OFF = "1"
$env:WECOM_PUSH_OFF = "1"
$env:SCHEDULER_ENABLED = "0"
$env:FILE_ROOT = "data/iso-files-$Db"
$serverLog = Join-Path $logDir "iso-$Db-$Port.log"
$server = Start-Process -FilePath ".\.venv\Scripts\python.exe" `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$Port") `
    -PassThru -NoNewWindow -RedirectStandardOutput $serverLog -RedirectStandardError "$serverLog.err"

$apiBase = "http://127.0.0.1:$Port/api/v1"
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 1
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$Port/docs" -UseBasicParsing -TimeoutSec 2 | Out-Null
        $ready = $true
        break
    } catch { }
}
if (-not $ready) {
    Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    Pop-Location
    throw "后端没起来（端口 $Port）。日志：$serverLog / $serverLog.err"
}
Write-Host "backend ready: $apiBase"

Write-Host "== 4. 接口回归 =="
if ($Suites.Count -eq 0) {
    $Suites = Get-Content (Join-Path $root "ops/check_suites.txt") |
        Where-Object { $_ -notmatch '^\s*(#|$)' } | ForEach-Object { $_.Trim() }
}
$env:API_BASE = $apiBase
$env:TEST_RUN_STARTED_AT = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss+00:00")
# Windows 控制台默认代码页是 GBK，而套件里会打印 ✓ / ✗ 这类字符；
# Python 按 locale 编码输出会直接 UnicodeEncodeError（断言全绿，退出码却是 1）。
# 强制 UTF-8，与 CI（Linux）一致：这里的退出码必须只反映断言结果。
$env:PYTHONIOENCODING = "utf-8"
$failed = @()
foreach ($suite in $Suites) {
    $file = "scripts/$suite.py"
    if (-not (Test-Path $file)) { Write-Host "FAIL $suite（脚本不存在）"; $failed += $suite; continue }
    $suiteLog = Join-Path $logDir "iso-$Db-$suite.log"
    & .\.venv\Scripts\python.exe $file *> $suiteLog
    if ($LASTEXITCODE -eq 0) { Write-Host "OK   $suite" } else { Write-Host "FAIL $suite（详情：$suiteLog）"; $failed += $suite }
}

Write-Host "== 5. 清扫本轮留痕 =="
& .\.venv\Scripts\python.exe scripts/clean_test_run_leftovers.py *> (Join-Path $logDir "iso-$Db-clean.log")

if (-not $KeepServer) {
    Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    Write-Host "已停后端（pid $($server.Id)）"
} else {
    Write-Host "后端保留在 $apiBase（pid $($server.Id)）"
}
Pop-Location

if (-not $KeepDb) {
    docker exec $pgContainer psql -U $pgUser -d postgres -c "DROP DATABASE IF EXISTS $Db;" 2>$null | Out-Null
    Write-Host "已删除一次性库 $Db"
} else {
    Write-Host "库保留：$Db"
}

if ($failed.Count -gt 0) {
    Write-Host "FAILED: $($failed -join ' ')"
    exit 1
}
Write-Host "全部通过 OK"
