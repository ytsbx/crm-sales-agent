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
    async with SessionLocal() as s:
        result = await s.execute(
            text(
                "UPDATE sample_requests SET status='approved', approved_at=:now "
                "WHERE id=:id AND status IN ('pending','rejected')"
            ),
            {"id": sample_id, "now": datetime.now(UTC)},
        )
        await s.commit()
        if result.rowcount != 1:
            print(f"样品 {sample_id} 未置为已批准（不存在，或状态不是 pending/rejected）")
            return 1
    print(f"样品 {sample_id} 已置为已批准")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
