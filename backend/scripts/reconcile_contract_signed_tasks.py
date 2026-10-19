"""清理合同删除后遗留的客户回签待办。

用法：
    DATABASE_URL=... PYTHONPATH=. .venv/bin/python scripts/reconcile_contract_signed_tasks.py
    DATABASE_URL=... PYTHONPATH=. .venv/bin/python scripts/reconcile_contract_signed_tasks.py --apply

默认只预览；只有显式 ``--apply`` 才会提交。脚本只处理合同回签系统待办，
不会碰人工任务、已完成任务或仍存在的合同。
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from app.core.database import SessionLocal, engine


SQL = text(
    """
    UPDATE tasks AS t
       SET status = 'cancelled',
           completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
           completion_note = '合同已删除，历史残留待办自动取消'
     WHERE t.source_key LIKE 'contract:signed-upload:%'
       AND t.status IN ('pending', 'doing')
       AND NOT EXISTS (
           SELECT 1
             FROM contract_documents AS d
            WHERE d.id = t.source_business_id
              AND d.deleted_at IS NULL
       )
    RETURNING t.id, t.source_business_id
    """
)


async def main(apply: bool) -> int:
    async with SessionLocal() as session:
        if apply:
            rows = (await session.execute(SQL)).all()
            await session.commit()
            print(f"已取消 {len(rows)} 条合同回签残留待办")
            if rows:
                print("任务 ID：" + ", ".join(str(row[0]) for row in rows))
        else:
            preview = text(
                """
                SELECT t.id
                  FROM tasks AS t
                 WHERE t.source_key LIKE 'contract:signed-upload:%'
                   AND t.status IN ('pending', 'doing')
                   AND NOT EXISTS (
                       SELECT 1 FROM contract_documents AS d
                        WHERE d.id = t.source_business_id AND d.deleted_at IS NULL
                   )
                ORDER BY t.id
                """
            )
            rows = (await session.execute(preview)).all()
            print(f"预计取消 {len(rows)} 条；确认后加 --apply 执行")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="提交清理，不加此参数只预览")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.apply)))
