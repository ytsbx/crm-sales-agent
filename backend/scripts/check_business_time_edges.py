"""业务时间边界：把第九批复审 §9.10 的四处遗漏钉成断言。

**只在隔离库跑**（会建一条成本记录并删掉）：必须显式给 `DATABASE_URL`
（库名以 `crm_iso` / `crm_check` 开头）。

## 这一套验的是什么

复审报了三处"还在用 UTC / 宿主机时区"的地方，加上自查发现的同类一处：

1. 合同模板与业务文件模板的 `{{today}}`（两个**渲染入口**）；
2. 成本失效入口写入的截止日期；
3. 统计明细 / 时间线的**展示**时间（无参 `astimezone()` 跟宿主机走）；
4. 导入时"不能是未来日期"的判断（同类，自查发现）。

## 怎么"固定时间"

不是给每个模块单独打补丁，而是**把 `core/timebase` 里的 `datetime.now()` 钉死** ——
所有业务日期都从 `timebase.today_business()` 出来，各模块
`from app.core.timebase import today_business` 拿到的仍是同一个函数、
函数体里读的就是被替掉的那个 `datetime`。这样验的是**真实调用链**，
而不是"我给每个模块塞了个假函数"（那种测法测不出漏网的入口）。

## 跑法

    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 \\
      DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_iso_test \\
      PYTHONPATH=. .venv/bin/python scripts/check_business_time_edges.py
"""

import asyncio
import os
import sys
import time as time_module
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal

import app.main  # noqa: F401  保证所有模型都注册进 metadata

from sqlalchemy import select
from starlette.requests import Request

from app.core import timebase
from app.core.database import SessionLocal, engine
from app.core.deps import CurrentUser
from app.core.importing import RowErrors
from app.modules.analytics import targets as targets_service
from app.modules.bizdoc import tokens as bizdoc_tokens
from app.modules.contract import service as contract_service
from app.modules.customer.model import Customer
from app.modules.pricing import router as pricing_router
from app.modules.pricing import service as pricing_service
from app.modules.pricing.model import ProductCost
from app.modules.timeline import service as timeline_service
from app.modules.user.model import User
from app.modules.product.model import Sku

FAILURES: list[str] = []
PREFIX = "CHKTIME"
BIZ = timebase.BUSINESS_TZ


def check(label: str, actual, expected) -> None:
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def require_isolated_db() -> str:
    """显式要求一次性隔离库：本套件会建成本记录。"""
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise SystemExit("必须显式设置 DATABASE_URL（一次性隔离库，库名以 crm_iso / crm_check 开头）")
    name = url.rsplit("/", 1)[-1].split("?")[0]
    if not name.startswith(("crm_iso", "crm_check")):
        raise SystemExit(f"拒绝执行：DATABASE_URL 指向 {name!r}，不是一次性隔离库")
    return name


def beijing(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """北京时间某时刻对应的 **UTC 瞬时**（不用手算时差，免得自己算错）。"""
    return datetime(year, month, day, hour, minute, tzinfo=BIZ).astimezone(UTC)


@contextmanager
def frozen_business_clock(instant: datetime):
    """把 `core/timebase` 读到的"现在"钉死在 `instant`。

    只替掉 `timebase` 模块全局里那个 `datetime` 名字：模块里 `now_business()`
    读的就是它，于是 `today_business()` / `month_key()` 等**全部**跟着冻结，
    而各业务模块 `from ... import today_business` 拿到的仍是同一个函数 ——
    漏网的入口照样会暴露出来。
    """
    original = timebase.datetime

    class _FrozenDatetime:  # 只拦 now()，其余属性转发给真正的 datetime
        def now(self, tz=None):
            return instant.astimezone(tz) if tz is not None else instant

        def __getattr__(self, name):
            return getattr(original, name)

    timebase.datetime = _FrozenDatetime()  # type: ignore[assignment]
    try:
        yield
    finally:
        timebase.datetime = original  # type: ignore[assignment]


def _fake_request() -> Request:
    """够 `client_ip()` 用的最小请求对象。"""
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/costs/1/expire",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


# --------------------------------------------------------------------------
# ① 两个模板渲染入口的 {{today}}
# --------------------------------------------------------------------------

def render_contract_template(body: str) -> str:
    text, _missing, _extra = contract_service.render_template(
        body, customer=Customer(name=f"{PREFIX}时间客户")
    )
    return text


def render_bizdoc_template(body: str) -> str:
    text, _unresolved = bizdoc_tokens.resolve_tokens(body, {})
    return text


def check_template_today() -> None:
    """北京时间当天 00:00 / 01:00 / 07:59 / 白天 / 元旦凌晨，都该填**北京那天**。"""
    cases = [
        ("北京时间 00:00（刚过零点）", beijing(2026, 10, 7, 0), "2026-10-07"),
        ("北京时间 01:00（UTC 还在前一天）", beijing(2026, 10, 7, 1), "2026-10-07"),
        ("北京时间 07:59（UTC 仍是前一天）", beijing(2026, 10, 7, 7, 59), "2026-10-07"),
        ("北京时间 12:00（普通白天）", beijing(2026, 10, 7, 12), "2026-10-07"),
        ("北京时间元旦凌晨 01:00（跨年）", beijing(2026, 1, 1, 1), "2026-01-01"),
    ]
    for label, instant, expected in cases:
        with frozen_business_clock(instant):
            check(f"合同模板 {{today}}：{label}", render_contract_template("{{today}}"), expected)
            check(
                f"业务文件模板 {{today}}：{label}",
                render_bizdoc_template("{{today}}"),
                expected,
            )


# --------------------------------------------------------------------------
# ② 成本失效入口
# --------------------------------------------------------------------------

async def check_cost_expire() -> None:
    """凌晨做的失效操作，写进去的截止日期应当是**北京那天**，且与取价判断一致。"""
    async with SessionLocal() as session:
        admin = (await session.execute(select(User).where(User.username == "admin"))).scalars().first()
        if admin is None:
            raise SystemExit("库里没有 admin 账号，先跑 scripts/seed.py")
        sku = (
            await session.execute(select(Sku).where(Sku.deleted_at.is_(None)).limit(1))
        ).scalars().first()
        if sku is None:
            raise SystemExit("库里没有 SKU，先跑 scripts/seed.py")

        cost = ProductCost(
            sku_id=sku.id,
            purchase_cost=Decimal("10"),
            # 起始日就设成**冻结的今天**：这样它按 `effective_from` 排序最新，
            # 取价接口会选中它。否则 seed 里那条旧成本会排在前面，
            # 断言就变成"在验 seed 的数据"了（第一版就是这么错的）。
            effective_from=date(2026, 10, 7),
            remark=f"{PREFIX}边界测试成本（跑完即删）",
            created_by=admin.id,
        )
        session.add(cost)
        await session.flush()
        cost_id, sku_id = cost.id, sku.id

        user = CurrentUser(admin, set(), ["admin"], "all")
        # 北京时间 2026-10-07 01:00 —— 此刻 UTC 还停在 10-06
        with frozen_business_clock(beijing(2026, 10, 7, 1)):
            await pricing_router.expire_cost(cost_id, _fake_request(), user, session)
            await session.commit()

        saved = (
            await session.execute(select(ProductCost).where(ProductCost.id == cost_id))
        ).scalars().first()
        check("成本失效入口写入的截止日期 = 北京那天", saved.effective_to, date(2026, 10, 7))

        # 与取价判断一致：当天仍算生效（截止日是**含**当天），第二天才失效
        same_day = await pricing_service.get_effective_cost(session, sku_id, date(2026, 10, 7))
        next_day = await pricing_service.get_effective_cost(session, sku_id, date(2026, 10, 8))
        check(
            "失效当天取到的仍是这条成本（截止日含当天）",
            same_day is not None and same_day.id == cost_id,
            True,
        )
        check(
            "第二天不再取到这条成本（区间已结束）",
            next_day is None or next_day.id != cost_id,
            True,
        )

        # 收尾：本套件自己造的成本自己删
        await session.execute(
            ProductCost.__table__.delete().where(ProductCost.id == cost_id)
        )
        await session.commit()


# --------------------------------------------------------------------------
# ③ 统计明细 / 时间线的展示时间
# --------------------------------------------------------------------------

def check_display_timezone_independent() -> None:
    """同一条时间戳，服务器时区设成 UTC 或 Asia/Shanghai，展示结果必须一样。"""
    instant = beijing(2026, 1, 1, 1)  # 库里存的是 2025-12-31 17:00 UTC
    expected = "2026-01-01 01:00"

    original_tz = os.environ.get("TZ")

    def probe(tz: str) -> tuple[str, str]:
        os.environ["TZ"] = tz
        time_module.tzset()
        return (
            targets_service._fmt_at(instant),
            timeline_service._human_time(instant),
        )

    try:
        under_utc = probe("UTC")
        under_shanghai = probe("Asia/Shanghai")
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time_module.tzset()

    check("统计明细展示（服务器 TZ=UTC）", under_utc[0], expected)
    check("时间线展示（服务器 TZ=UTC）", under_utc[1], expected)
    check("统计明细展示（服务器 TZ=Asia/Shanghai）", under_shanghai[0], expected)
    check("时间线展示（服务器 TZ=Asia/Shanghai）", under_shanghai[1], expected)
    check("两种服务器时区下结果一致", under_utc == under_shanghai, True)

    # date 类（发货日这种）不该被套时区、原样输出
    check("date 字段原样输出（不套时区）", targets_service._fmt_at(date(2026, 1, 5)), "2026-01-05")


# --------------------------------------------------------------------------
# ④ 导入的「不能是未来日期」判断（自查发现的同类）
# --------------------------------------------------------------------------

def check_import_future_guard() -> None:
    """北京时间凌晨导入"今天"的日期，不该被当成未来日期拒掉。"""
    with frozen_business_clock(beijing(2026, 10, 7, 1)):
        errs_today = RowErrors(row=1, name="今天")
        today_value = errs_today.date_value("2026-10-07", "生效起始日", allow_future=False)

        errs_future = RowErrors(row=2, name="明天")
        future_value = errs_future.date_value("2026-10-08", "生效起始日", allow_future=False)

    check("北京今天不算未来日期（收下）", today_value, date(2026, 10, 7))
    check("北京明天才是未来日期（拒掉）", future_value, None)
    check("拒掉时给出可读原因", bool(errs_future.reasons), True)


async def main() -> None:
    db_name = require_isolated_db()
    print(f"隔离库：{db_name}")
    print(f"业务时区：{timebase.BUSINESS_TZ_NAME}")

    print("\n① 两个模板渲染入口的 {{today}}")
    check_template_today()

    print("\n② 成本失效入口的截止日期")
    await check_cost_expire()

    print("\n③ 统计明细 / 时间线的展示时间")
    check_display_timezone_independent()

    print("\n④ 导入的「未来日期」判断")
    check_import_future_guard()

    await engine.dispose()

    print()
    if FAILURES:
        print(f"❌ 业务时间边界回归失败 {len(FAILURES)} 项：")
        for item in FAILURES:
            print(f"   - {item}")
        sys.exit(1)
    print("✅ 业务时间边界回归通过")


if __name__ == "__main__":
    asyncio.run(main())
