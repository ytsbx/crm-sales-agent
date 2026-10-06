#!/usr/bin/env bash
# CRM 监控巡检：可放进 cron（每 5 分钟）或 systemd timer。
#
#   */5 * * * * /opt/crm/ops/deploy/monitor.sh >> /var/log/crm-monitor.log 2>&1
#
# 检查项：
#   1. 后端 /health（进程 + 数据库连通性，见 backend/app/main.py）
#   2. 磁盘剩余空间（附件/备份都在本机磁盘，写满即事故）
#   3. 备份状态：**区分"过期"和"失败"**——
#        失败  = 备份目录里有 manifest.json 但自校验不过（数据库/文件缺一边、摘要不符、
#                清单损坏）→ 这类备份不能用来恢复，必须立刻处理；
#        过期  = 最新一份完整备份是 25 小时前的（昨晚没跑成，或定时器没触发）；
#        未跑过 = 一个完整备份都没有（新机器/目录配错）。
#      原来的实现只按"25 小时内有 .dump 文件"判断，于是三种情况都是同一句
#      "昨晚的备份可能没跑成"：只有数据库的半个备份会被当成正常，磁盘上放一个
#      损坏的旧 dump 也会被当成正常——这正是 8.16 要求区分的两种状态。
#   4. 失败现场：.crm-*.partial 目录（备份中途失败留下的），报出来便于定位
#         "连续几天都在同一处失败"。
#
# 告警出口：配置了 CRM_ALERT_WEBHOOK（企业微信群机器人 webhook 地址）就推群；
# 没配置则只写 stdout/日志。退出码 1 = 有故障，便于接任意告警系统。

set -uo pipefail

BACKEND_URL="${CRM_BACKEND_URL:-http://127.0.0.1:8000}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/crm}"
DISK_MIN_GB="${DISK_MIN_GB:-5}"
#: 备份允许的最大年龄（小时）。默认 25 小时 = 每天 02:30 跑一次 + 一点裕量。
MAX_BACKUP_AGE_HOURS="${MAX_BACKUP_AGE_HOURS:-25}"
WEBHOOK="${CRM_ALERT_WEBHOOK:-}"
#: 备份自校验脚本：默认取与本脚本同目录的 verify_backup.sh（部署时一起同步到
#: /opt/crm/ops/deploy/ 即可，少一个要维护的绝对路径）；特殊布局用环境变量覆盖。
VERIFY_BACKUP="${VERIFY_BACKUP:-$(cd "$(dirname "$0")" && pwd)/verify_backup.sh}"

PROBLEMS=()

alert() {
  local text="$1"
  echo "[$(date '+%F %T')] ALERT $text"
  if [[ -n "$WEBHOOK" ]]; then
    curl -sf -X POST "$WEBHOOK" \
      -H 'Content-Type: application/json' \
      -d "{\"msgtype\":\"text\",\"text\":{\"content\":\"[CRM] $text\"}}" \
      >/dev/null || echo "[$(date '+%F %T')] ALERT 告警推送失败（webhook 不可达）"
  fi
}

# 1. 后端健康（含数据库探活）
health_json=$(curl -sf --max-time 10 "$BACKEND_URL/health" 2>/dev/null) || health_json=""
if [[ -z "$health_json" ]]; then
  PROBLEMS+=("后端不可达（$BACKEND_URL/health 无响应）——进程可能挂了")
else
  if ! grep -q '"status": *"up"' <<<"$health_json" && ! grep -q '"status":"up"' <<<"$health_json"; then
    PROBLEMS+=("后端健康检查异常：$health_json")
  fi
fi

# 2. 磁盘空间（df -k 的 KB 换算成 GB，GNU/BSD 通用）
disk_free_gb=$(df -k / | awk 'NR==2 {print int($4/1048576)}')
if [[ "${disk_free_gb:-0}" -lt "$DISK_MIN_GB" ]]; then
  PROBLEMS+=("根分区剩余 ${disk_free_gb}GB，低于阈值 ${DISK_MIN_GB}GB（附件/备份会写满）")
fi

# 3. 备份状态：先按"完整备份目录"（有 manifest.json）挑最新一份，再自校验。
#    排序用目录名里的时间戳（crm-YYYYmmdd-HHMMSS），不依赖 mtime——
#    rsync/复制/解包会改 mtime，但目录名里的备份时刻不会变。
latest=""
if [[ -d "$BACKUP_DIR" ]]; then
  # 用 shell 通配挑"有 manifest.json 的备份目录"：不依赖 find 的 -exec，
  # 也不需要外部命令；隐藏的 .crm-*.partial 目录不会被 crm-* 匹配到。
  for m in "$BACKUP_DIR"/crm-*/manifest.json; do
    [[ -f "$m" ]] || continue
    latest="${m%/manifest.json}"
  done
fi

# 备份年龄（小时）：从目录名的时间戳算，而不是文件 mtime——复制/解包会改 mtime，
# 用 mtime 判断"过期"会把一份刚拷到灾备机的旧备份算成新的。date 的解析能力在
# GNU 和 BSD 上不兼容，所以用 bash 内建算术自己算，避免依赖某个平台的 date。
backup_age_hours() { # backup_age_hours <crm-YYYYmmdd-HHMMSS 目录>
  local name="${1##*/}" ts
  ts="${name#crm-}"
  if [[ ! "$ts" =~ ^[0-9]{8}-[0-9]{6}$ ]]; then
    printf '%s\n' "-1"    # 名字不符合备份命名：让调用方报"人工确认"
    return 0
  fi
  local b_y="${ts:0:4}" b_m="${ts:4:2}" b_d="${ts:6:2}" b_H="${ts:9:2}" b_M="${ts:11:2}" b_S="${ts:13:2}"
  # 先归一到 UTC 再比较：$(date -u +%s) 与 10# 强制十进制（避免 08/09 被当成八进制）。
  local b_epoch n_epoch
  b_epoch="$(TZ=UTC date -d "${b_y}-${b_m}-${b_d} ${b_H}:${b_M}:${b_S}" +%s 2>/dev/null || true)"
  if [[ -z "$b_epoch" ]]; then
    # BSD/macOS 的 date 不吃 -d；用 python3 兜底（部署机上是常备的）。
    if command -v python3 >/dev/null 2>&1; then
      b_epoch="$(python3 -c 'import datetime,sys;print(int(datetime.datetime.strptime(sys.argv[1],"%Y%m%d-%H%M%S").replace(tzinfo=datetime.timezone.utc).timestamp()))' "$ts" 2>/dev/null || true)"
    fi
  fi
  if [[ -z "$b_epoch" ]]; then
    printf '%s\n' "-1"
    return 0
  fi
  n_epoch="$(date -u +%s)"
  printf '%s\n' "$(( (n_epoch - b_epoch) / 3600 ))"
}

if [[ -z "$latest" ]]; then
  # 没有任何完整备份：可能是从没跑过，也可能只有"只有 dump"的旧式备份。
  legacy_dump=$(find "$BACKUP_DIR" -maxdepth 1 -name 'crm-*.dump' -type f 2>/dev/null | head -1)
  if [[ -n "$legacy_dump" ]]; then
    PROBLEMS+=("备份状态=失败：$BACKUP_DIR 里只有数据库 dump（$(basename "$legacy_dump")）没有完整恢复清单，恢复后文件原件会缺失；请跑一次 backup_db.sh")
  else
    PROBLEMS+=("备份状态=从未成功：$BACKUP_DIR 下没有任何完整备份目录（含 manifest.json）——定时器是否已启用？见 systemctl list-timers | grep crm")
  fi
else
  # 失败优先于过期：一份"损坏的今天的新备份"比"三天前的旧备份"更危险，
  # 因为它看上去是新的。先判失败，再判新鲜度。
  if [[ -f "$VERIFY_BACKUP" ]]; then
    if ! verify_out=$(bash "$VERIFY_BACKUP" --backup "$latest" --quiet 2>&1); then
      PROBLEMS+=("备份状态=失败：最新完整备份 $(basename "$latest") 自校验不通过——$(printf '%s' "$verify_out" | grep -m1 '^PROBLEM' || printf '%s' "$verify_out" | tail -1)")
    fi
  else
    PROBLEMS+=("无法校验备份：找不到 $VERIFY_BACKUP（监控不能只凭文件名判断备份好坏，请同步部署 ops/deploy/ 或设 VERIFY_BACKUP）")
  fi
  age="$(backup_age_hours "$latest")"
  if [[ "$age" == "-1" ]]; then
    PROBLEMS+=("备份状态=可疑：$(basename "$latest") 的目录名不符合 crm-YYYYmmdd-HHMMSS，无法判断备份有多旧，请人工确认")
  elif [[ "$age" -gt "$MAX_BACKUP_AGE_HOURS" ]]; then
    PROBLEMS+=("备份状态=过期：最新完整备份是 $(basename "$latest")，已 ${age} 小时没有新备份（阈值 ${MAX_BACKUP_AGE_HOURS} 小时；备份任务没跑成或定时器未触发）")
  fi
fi

# 4. 失败现场：备份中途失败会留下 .crm-*.partial 目录。连续出现说明每次都挂在同一步，
#    必须报出来，否则只会看到"备份过期"，不知道卡在哪。
partial_count=$(find "$BACKUP_DIR" -maxdepth 1 -type d -name '.crm-*.partial' 2>/dev/null | wc -l | tr -d ' ')
if [[ "${partial_count:-0}" -gt 0 ]]; then
  newest_partial=$(find "$BACKUP_DIR" -maxdepth 1 -type d -name '.crm-*.partial' 2>/dev/null | sort | tail -1)
  PROBLEMS+=("备份失败现场：$BACKUP_DIR 下有 ${partial_count} 个未发布目录（最新 $(basename "$newest_partial")），说明备份在发布前就失败了，请查看 journalctl -u crm-backup -n 100")
fi

if [[ ${#PROBLEMS[@]} -gt 0 ]]; then
  for p in "${PROBLEMS[@]}"; do alert "$p"; done
  exit 1
fi
echo "[$(date '+%F %T')] OK 后端/数据库/磁盘/备份（数据库+文件+清单齐全且校验通过，最新 $(basename "$latest")）均正常"
