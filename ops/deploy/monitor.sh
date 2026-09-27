#!/usr/bin/env bash
# CRM 监控巡检：可放进 cron（每 5 分钟）或 systemd timer。
#
#   */5 * * * * /opt/crm/ops/deploy/monitor.sh >> /var/log/crm-monitor.log 2>&1
#
# 检查项：
#   1. 后端 /health（进程 + 数据库连通性，见 backend/app/main.py）
#   2. 磁盘剩余空间（附件/备份都在本机磁盘，写满即事故）
#   3. 最新数据库备份的新鲜度（超过 25 小时 = 昨晚备份没跑成）
#
# 告警出口：配置了 CRM_ALERT_WEBHOOK（企业微信群机器人 webhook 地址）就推群；
# 没配置则只写 stdout/日志。退出码 1 = 有故障，便于接任意告警系统。

set -uo pipefail

BACKEND_URL="${CRM_BACKEND_URL:-http://127.0.0.1:8000}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/crm}"
DISK_MIN_GB="${DISK_MIN_GB:-5}"
WEBHOOK="${CRM_ALERT_WEBHOOK:-}"

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

# 3. 备份新鲜度：25 小时内必须有新备份（find -mmin，GNU/BSD 通用）
if [[ -d "$BACKUP_DIR" ]]; then
  recent=$(find "$BACKUP_DIR" -name 'crm-*.dump' -type f -mmin -1500 2>/dev/null | head -1)
  if [[ -z "$recent" ]]; then
    PROBLEMS+=("25 小时内没有新备份（${BACKUP_DIR}），昨晚的备份可能没跑成")
  fi
fi

if [[ ${#PROBLEMS[@]} -gt 0 ]]; then
  for p in "${PROBLEMS[@]}"; do alert "$p"; done
  exit 1
fi
echo "[$(date '+%F %T')] OK 后端/数据库/磁盘/备份均正常"
