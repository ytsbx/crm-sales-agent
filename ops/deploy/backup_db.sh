#!/usr/bin/env bash
# 数据库每日备份：pg_dump 自定义格式压缩 + 恢复校验 + 过期清理。
#
# 用法（手动）：
#   bash ops/deploy/backup_db.sh
# 生产（Linux）：由 crm-backup.timer 每天 02:30 自动跑（见同目录 timer）。
#
# 设计：
# - 密码/连接串从环境变量 DATABASE_URL 读（.env 里的 +asyncpg 后缀自动剥掉，
#   pg_dump 不认 SQLAlchemy 方言）；
# - -Fc 自定义格式：压缩率高、支持单表恢复和 pg_restore 并行恢复；
# - 备完立刻 pg_restore --list 校验——备份文件打不开等于没备份，必须在备份时发现；
# - 保留 KEEP_DAYS 天，更早的自动删除。
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/crm}"
KEEP_DAYS="${KEEP_DAYS:-14}"
# .env 里是 postgresql+asyncpg://...，剥掉 +asyncpg 给 pg_dump 用
DB_URL="${DATABASE_URL:-postgresql://crm:crm123456@127.0.0.1:5432/crm_sales_agent}"
DB_URL="${DB_URL//+asyncpg/}"

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/crm-$STAMP.dump"

pg_dump "$DB_URL" -Fc -f "$OUT"

# 写完必须能读，否则报警退出（systemd 会标记失败 → 监控能看到）
if ! pg_restore --list "$OUT" > /dev/null 2>&1; then
  echo "FAILED: 备份文件校验失败 $OUT" >&2
  exit 1
fi

SIZE=$(du -h "$OUT" | cut -f1)
echo "OK $OUT ($SIZE)"

find "$BACKUP_DIR" -name 'crm-*.dump' -type f -mtime +"$KEEP_DAYS" -delete
