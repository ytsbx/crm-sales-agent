"""测试造数：把样品置为「已批准」（冒烟/时间线夹具用）。

## 为什么需要它

打样审批自 bdd6efc9 起统一收口到同序 OA（本地 POST /samples/{id}/approve 一律
409「打样审批统一在同序 OA 办理」），approved 状态只由 OA 审批同步写入。
冒烟与 CI 都没有外部 OA 可用，而客户时间线夹具必须走完 made→ship→sign→confirm
（SAMPLE_TRANSITIONS 状态机要求先 approved）——按项目规矩「行为变更要改夹具，
不是放宽规则」，这里直插状态，是旧 approve 端点的等价最小写入
（status + approved_at），**不动产品代码、不给收口开口子**。

## 安全前提

只许连一次性测试库（require_isolated_db），且只动 pending/rejected 的样品
（照 SAMPLE_TRANSITIONS 的合法入口：pending→approved、rejected→approved）。
0 行更新视为失败退出——静默没生效比报错更难查。

    PYTHONPATH=. .venv/bin/python scripts/mark_sample_approved.py <sample_id>
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import text

from _test_support import require_isolated_db

# 这个脚本会真的改数据 —— 决不允许连到正式库（见 _test_support 的「防呆」一节）。
# 必须排在 `from app.*` 之前：settings 在 import 时就定下了连哪个库。
require_isolated_db()

from app.core.database import SessionLocal  # noqa: E402


async def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].isdigit() or int(sys.argv[1]) <= 0:
        print("用法：mark_sample_approved.py <sample_id>（正整数）")
        return 2
    sample_id = int(sys.argv[1])
    # 传真的 datetime 出去：asyncpg 不接受字符串形式的时间参数（clean_test_run_leftovers 同款教训）。
    now = datetime.now(UTC)
    async with SessionLocal() as s:
        row = (
            await s.execute(
                text("SELECT version FROM sample_requests WHERE id=:id"),
                {"id": sample_id},
            )
        ).first()
        if row is None:
            print(f"样品 {sample_id} 不存在")
            return 1
        # 两条一起写：样品状态（状态机入口 pending/rejected→approved）+ 一条 approved 的
        # OA 审批记录（made/ship 的 require_sample_approved 闸门查的是 oa_instances，
        # 光改 sample_requests.status 过不了闸 —— CI 实证踩过）。
        result = await s.execute(
            text(
                "UPDATE sample_requests SET status='approved', approved_at=:now "
                "WHERE id=:id AND status IN ('pending','rejected')"
            ),
            {"id": sample_id, "now": now},
        )
        await s.execute(
            text(
                "INSERT INTO oa_instances "
                "(inquiry_id, inquiry_version, oa_type, idempotency_key, submit_round, provider, status, created_at, updated_at) "
                "VALUES (:sid, :ver, 'sample', :key, 1, 'self_oa', 'approved', :now, :now)"
            ),
            {
                "sid": sample_id,
                "ver": int(row.version or 1),
                "key": f"smoke-mark-{sample_id}-{uuid4().hex}",
                "now": now,
            },
        )
        await s.commit()
        if result.rowcount != 1:
            print(f"样品 {sample_id} 未置为已批准（状态不是 pending/rejected）")
            return 1
    print(f"样品 {sample_id} 已置为已批准（含 OA 审批记录）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
