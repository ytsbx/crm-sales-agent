"""把业务口径复位成「只做国内」（回归开跑前的统一兜底）。

有套件会临时把口径放开成「国内与出口都做」才能造外币数据（那正是业务方的
做法，见 `scripts/_fx_scope.py`）。万一某个套件被硬杀（超时 / SIGKILL）
没来得及收回来，**后面所有套件都会以为可以写外币** —— 外币断言会莫名其妙
地绿，甚至悄悄往库里写外币。所以 `ops/run_checks.sh` 在跑清单之前先复位一次。

按 id 幂等：本来就已经是 `domestic` 时什么都不改（只回一句"已经是"），
所以不会产生多余的审计留痕。
"""

import asyncio

from sqlalchemy import select

from _test_support import require_isolated_db

# 它会改系统配置 —— 决不允许连到正式库（见 _test_support 的「防呆」一节）。
# 必须排在 `from app.*` 之前：settings 在 import 时就定下了连哪个库。
require_isolated_db()

from app.core.database import SessionLocal  # noqa: E402
from app.modules.settings.model import SystemSetting  # noqa: E402
from app.modules.settings.service import DEFAULT_SETTINGS  # noqa: E402


async def main() -> int:
    wanted = (DEFAULT_SETTINGS.get("trade_mode") or {}).get("mode", "domestic")
    async with SessionLocal() as session:
        row = (
            await session.execute(select(SystemSetting).where(SystemSetting.key == "trade_mode"))
        ).scalar_one_or_none()
        current = ((row.value if row is not None else None) or {}).get("mode")
        if current == wanted:
            print(f"业务口径已经是 {current!r}，不用动")
            return 0
        if row is None:
            session.add(SystemSetting(key="trade_mode", value={"mode": wanted}))
        else:
            # 重新赋值而不是原地改字段：JSONB 的原地修改不会被 SQLAlchemy 认成"变了"
            row.value = {**(row.value or {}), "mode": wanted}
        await session.commit()
        print(f"业务口径已从 {current!r} 复位成 {wanted!r}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
