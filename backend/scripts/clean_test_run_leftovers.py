"""收尾清扫：把**本轮回归跑出来的**站内通知与系统留痕清掉。

## 为什么需要它

各套件会跑真实业务流程（提交审批、建单、打样、导出告警），这些流程会顺手生成
站内通知和客户时间线留痕——它们**没有 CHK 前缀**，而且判据常常挂在套件自己
马上要删掉的父表上（比如"通知的 business_id 在本套件的报价里"），顺序一错就
查空，于是每跑一轮就留一批。逐个套件打补丁又啰嗦又容易漏（实测修完仍有
"孤儿"通知：关联的订单早被删了，通知还挂着）。

这里换成统一兜底：按**本轮的时间窗**清扫。时间窗之前的一律不动。

## 安全前提（重要）

**必须显式给出时间窗**（环境变量 `TEST_RUN_STARTED_AT`）。没有就拒绝执行并
以非零退出——这样它绝不可能在没有明确范围的情况下误删业务数据。
`ops/run_checks.sh` 在开跑时记下时刻并自动传入。

    TEST_RUN_STARTED_AT=2026-09-30T10:40:00+08:00 \
        PYTHONPATH=. .venv/bin/python scripts/clean_test_run_leftovers.py
"""

import asyncio
import os
import sys
from datetime import datetime

from sqlalchemy import text

from _test_support import require_isolated_db

# 这个脚本会真的删数据 —— 决不允许连到正式库（见 _test_support 的「防呆」一节）。
# 必须排在 `from app.*` 之前：settings 在 import 时就定下了连哪个库。
require_isolated_db()

from app.core.database import SessionLocal  # noqa: E402

ENV_KEY = 'TEST_RUN_STARTED_AT'


async def main() -> int:
    started_at = (os.environ.get(ENV_KEY) or '').strip()
    if not started_at:
        print(
            f"拒绝执行：没有给 {ENV_KEY}。这个脚本按时间窗删数据，"
            "必须由 run_checks.sh 传明确的起点，避免误删业务数据。"
        )
        return 2
    # 传真的 datetime 出去：asyncpg 不接受字符串形式的时间参数（真踩过，
    # 见 check_quote_center_acceptance 里同一处教训）。
    started = datetime.fromisoformat(started_at)

    async with SessionLocal() as s:
        row = (
            await s.execute(
                text(
                    "with d1 as (delete from notifications where created_at >= :t returning 1), "
                    "d2 as (delete from followups where followup_type = '系统' "
                    "and created_at >= :t returning 1) "
                    "select (select count(*) from d1) as n, (select count(*) from d2) as f"
                ),
                {'t': started},
            )
        ).one()
        await s.commit()

    print(f"  清扫本轮通知 {row.n} 条、系统留痕 {row.f} 条（时间窗起点 {started_at}）")
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
