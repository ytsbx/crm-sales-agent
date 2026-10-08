"""业务时间边界：把第九批复审 §9.10 的四处遗漏钉成断言。

**只在隔离库跑**（会建一条成本记录并删掉）：必须显式给 `DATABASE_URL`
（库名以 `crm_iso` / `crm_check` 开头）。

## 这一套验的是什么

复审报了三处"还在用 UTC / 宿主机时区"的地方，加上自查发现的同类一处：

1. 合同模板与业务文件模板的 `{{today}}`（两个**渲染入口**）；
2. 成本失效入口写入的截止日期；
3. 统计明细 / 时间线的**展示**时间（无参 `astimezone()` 跟宿主机走）；
4. 导入时"不能是未来日期"的判断（同类，自查发现）；
5. 商机**两个成交入口**的有效期判断（本次复核查出：旧的「标记成交」单独写了
   一段 `now(UTC).date()`，而 confirm-win 那条另写一段，两处迟早会漂）。

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
from _test_support import require_isolated_db

require_isolated_db()

import app.main  # noqa: F401  保证所有模型都注册进 metadata

from sqlalchemy import select, text
from starlette.requests import Request

from app.core import timebase
from app.core.database import SessionLocal, engine
from app.core.deps import CurrentUser
from app.core.errors import AppError
from app.core.importing import RowErrors
from app.modules.analytics import targets as targets_service
from app.modules.bizdoc import tokens as bizdoc_tokens
from app.modules.contract import service as contract_service
from app.modules.customer.model import Customer
from app.modules.opportunity import router as opportunity_router
from app.modules.opportunity.model import Opportunity, OpportunityStage
from app.modules.opportunity.schema import OpportunityConfirmWin, OpportunityWin
from app.modules.pricing import router as pricing_router
from app.modules.pricing import service as pricing_service
from app.modules.pricing.model import ProductCost
from app.modules.quote.model import Quote, QuoteVersion
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
    """点"失效"= **人工立即停用**：当天立刻退出核价（第十一批 11.5）。

    ⚠️ 本节的断言在 2026-10-08 按新口径**改过一次**。
    原来断言的是"失效入口写入的截止日期 = 北京那天" + "失效当天仍算生效
    （截止日含当天）"—— 那套口径已被主人否掉：点失效就该立刻停用，不能拖到第二天。
    现在实现改为写 `stopped_at`、**不动 `effective_to`**（区间判据"含当天"对
    其他成本仍然成立，不能被这一条连累），所以断言跟着换成：

    - 写入了"人工停用时刻"；
    - `effective_to` **没被动过**（这是"没有连累正常区间语义"的证据）；
    - **当天立刻取不到**，第二天也取不到。

    北京时间这一层仍然要守：判据与"今天几号"无关，所以凌晨和白天结果必须一致 ——
    原来那个"凌晨写 UTC 日期会少一天"的坑，因为不再写日期而自然消失。
    """
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
        check("失效入口写入了「人工停用时刻」", saved.stopped_at is not None, True)
        check(
            "**没有**去改截止日（区间判据一个字没动，不连累正常设置的有效期）",
            saved.effective_to,
            None,
        )

        # 新口径：点完立刻停用，当天就取不到
        same_day = await pricing_service.get_effective_cost(session, sku_id, date(2026, 10, 7))
        next_day = await pricing_service.get_effective_cost(session, sku_id, date(2026, 10, 8))
        check(
            "★失效当天立刻取不到这条成本（旧口径是「当天仍生效」，按新口径已改）",
            same_day is None or same_day.id != cost_id,
            True,
        )
        check(
            "第二天同样取不到",
            next_day is None or next_day.id != cost_id,
            True,
        )

        # ---- 区间边界**没被连累**（复审第 4 条：不能为了一个"立即停用"
        #      把"截止日含当天"这条规则一起改掉）----
        natural = ProductCost(
            sku_id=sku_id,
            purchase_cost=Decimal("20"),
            effective_from=date(2026, 10, 1),
            effective_to=date(2026, 10, 7),  # 正常设置的区间，没被人停用
            remark=f"{PREFIX}区间边界（跑完即删）",
            created_by=admin.id,
        )
        session.add(natural)
        await session.flush()
        natural_id = natural.id
        boundary = await pricing_service.get_effective_cost(session, sku_id, date(2026, 10, 7))
        check(
            "正常设置的「截止日含当天」没被连累：当天仍取得到",
            boundary is not None and boundary.id == natural_id,
            True,
        )
        after = await pricing_service.get_effective_cost(session, sku_id, date(2026, 10, 8))
        check(
            "过了截止日自然取不到（区间语义照旧）",
            after is None or after.id != natural_id,
            True,
        )

        # ---- 重复点失效：幂等，不报错、也不改写第一次那一刻 ----
        first_stamp = saved.stopped_at
        await pricing_router.expire_cost(cost_id, _fake_request(), user, session)
        await session.commit()
        again = (
            await session.execute(select(ProductCost).where(ProductCost.id == cost_id))
        ).scalars().first()
        check("重复点失效 → 不报错，且不改写第一次的停用时刻", again.stopped_at, first_stamp)

        # 收尾：本套件自己造的成本自己删
        await session.execute(
            ProductCost.__table__.delete().where(ProductCost.id.in_([cost_id, natural_id]))
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


# --------------------------------------------------------------------------
# ⑤ 商机两个成交入口的有效期判断
# --------------------------------------------------------------------------

async def check_opportunity_win_entry_boundary() -> None:
    """成交入口的有效期判断必须按**业务日期（北京时间）**。

    复现单：北京时间 2026-01-01 01:00，报价有效期 2025-12-31（北京"昨天"）。
    旧的「标记成交」入口写的是 `datetime.now(UTC).date()` —— 那一刻 UTC 还停在
    2025-12-31，"昨天 < 今天"不成立，于是**已经过期的报价照样被标成交**。

    这里**直接调两个成交入口**（而不是只验 `quote_is_expired()` 这个工具函数），
    并把"有效期到昨天 → 拦"和"有效期到今天 → 按既有规则放行"两个方向都钉住。
    """
    async with SessionLocal() as session:
        admin = (
            await session.execute(select(User).where(User.username == "admin"))
        ).scalars().first()
        if admin is None:
            raise SystemExit("库里没有 admin 账号，先跑 scripts/seed.py")

        won_stage = (
            await session.execute(
                select(OpportunityStage).where(OpportunityStage.is_win.is_(True))
            )
        ).scalars().first()
        if won_stage is None:
            raise SystemExit("库里没有成交阶段（is_win），先跑 scripts/seed.py")
        open_stage = (
            await session.execute(
                select(OpportunityStage)
                .where(
                    OpportunityStage.status == "active",
                    OpportunityStage.is_win.is_(False),
                    OpportunityStage.is_loss.is_(False),
                )
                .order_by(OpportunityStage.sequence.asc())
                .limit(1)
            )
        ).scalars().first()
        if open_stage is None:
            raise SystemExit("库里没有可用的进行中阶段，先跑 scripts/seed.py")

        # confirm-win 第一步就查 order:manage，权限要一起给
        user = CurrentUser(admin, {"opportunity:manage", "order:manage"}, ["admin"], "all")
        request = _fake_request()

        customer = Customer(
            name=f"{PREFIX}成交边界客户", owner_id=admin.id, created_by=admin.id
        )
        session.add(customer)
        await session.flush()

        created_quote_ids: list[int] = []
        quote_seq = 0

        async def make_opportunity(title: str) -> Opportunity:
            opportunity = Opportunity(
                customer_id=customer.id,
                title=title,
                stage_id=open_stage.id,
                owner_id=admin.id,
                status="open",
            )
            session.add(opportunity)
            await session.flush()
            return opportunity

        async def make_quote(opportunity: Opportunity, valid_until: date) -> QuoteVersion:
            """造一份"走到成交入口就会撞有效期"的报价。

            `status="accepted"` 是刻意选的：两个入口都放行 accepted，而
            `accept_version()` 遇到 accepted 会在**不写任何东西**的情况下停住 ——
            正好用来确认 confirm-win 已经走过了有效期这一关。
            """
            nonlocal quote_seq
            quote_seq += 1
            stamp = datetime(2025, 12, 31, 2, 0, tzinfo=UTC)
            quote = Quote(
                quote_no=f"{PREFIX}-Q{quote_seq}",
                opportunity_id=opportunity.id,
                customer_id=customer.id,
                owner_id=admin.id,
                status="accepted",
                valid_until=valid_until,
                created_by=admin.id,
            )
            session.add(quote)
            await session.flush()
            version = QuoteVersion(
                quote_id=quote.id,
                version_no=1,
                approval_status="approved",
                sent_at=stamp,
                valid_until_snapshot=valid_until,
                created_at=stamp,
                created_by=admin.id,
            )
            session.add(version)
            await session.flush()
            quote.current_version_id = version.id
            await session.flush()
            created_quote_ids.append(quote.id)
            return version

        async def call_entry(entry: str, opportunity: Opportunity, version: QuoteVersion):
            """调**真实的**成交入口；被拦时返回错误说明，没被拦返回 None。"""
            try:
                if entry == "win":
                    await opportunity_router.win_opportunity(
                        opportunity.id,
                        OpportunityWin(
                            win_quote_version_id=version.id, remark=f"{PREFIX}边界"
                        ),
                        request,
                        user,
                        session,
                    )
                else:
                    await opportunity_router.confirm_win_and_create_order(
                        opportunity.id,
                        OpportunityConfirmWin(
                            win_quote_version_id=version.id, remark=f"{PREFIX}边界"
                        ),
                        request,
                        user,
                        session,
                    )
            except AppError as exc:
                return exc.message or ""
            return None

        opp_blocked = await make_opportunity(f"{PREFIX}成交边界-应拦")
        opp_pass = await make_opportunity(f"{PREFIX}成交边界-应放行")

        # ---- 方向一：有效期到"北京昨天" → 两个入口都必须拦 ----
        for label, instant, yesterday in (
            (
                "北京时间 2026-10-07 01:00（UTC 还停在前一天）",
                beijing(2026, 10, 7, 1),
                date(2026, 10, 6),
            ),
            ("元旦凌晨 2026-01-01 01:00（跨年）", beijing(2026, 1, 1, 1), date(2025, 12, 31)),
        ):
            version = await make_quote(opp_blocked, yesterday)
            with frozen_business_clock(instant):
                for entry, tag in (("win", "标记成交"), ("confirm", "确认成交并建单")):
                    message = await call_entry(entry, opp_blocked, version)
                    check(
                        f"{label}｜有效期到昨天 → {tag}被拦下",
                        message is not None and "已过有效期" in message,
                        True,
                    )
        check("被拦之后商机没有被标成交", opp_blocked.status, "open")

        # ---- 方向二：有效期到"北京今天" → 按既有规则（截止日**含**当天）放行 ----
        # ⚠️ 第十二批 12.2 起 `/win` 与 `/confirm-win` 是**同一套**流程：要定位有效
        # 报价版本，并走"客户确认 → 标成交 → 转订单"。所以"走到底、商机变成已成交"
        # 这一覆盖已搬到 `check_opportunity_gates`（那里连带验证建单）；
        # 本套件只关心**有效期这一关**是否按北京时间放行 —— 写法与下面 confirm-win 对齐。
        version = await make_quote(opp_pass, date(2026, 1, 1))
        with frozen_business_clock(beijing(2026, 1, 1, 1)):
            message = await call_entry("win", opp_pass, version)
        check(
            "元旦凌晨｜有效期到今天 → 标记成交不再被有效期拦下",
            "已过有效期" not in (message or ""),
            True,
        )
        check(
            "标记成交停在的确实是下一道「接受」闸门",
            bool(message) and "才能接受或拒绝" in (message or ""),
            True,
        )

        # confirm-win 的"放行"方向只验到**过了有效期这一关**为止：再往后它会真的
        # 接受报价并建单，那是别的套件的活。让它停在下一道「接受」闸门即可 ——
        # 能停在那里，就说明有效期这一关已经放行（否则报的会是"已过有效期"）。
        version = await make_quote(opp_blocked, date(2026, 1, 1))
        with frozen_business_clock(beijing(2026, 1, 1, 1)):
            message = await call_entry("confirm", opp_blocked, version)
        check(
            "元旦凌晨｜有效期到今天 → 确认成交不再被有效期拦下",
            message is not None and "已过有效期" not in message,
            True,
        )
        check(
            "确认成交停在的确实是下一道「接受」闸门",
            bool(message) and "才能接受或拒绝" in (message or ""),
            True,
        )

        # ---- 收尾：本套件自己造的夹具自己删 ----
        opp_ids = f"{opp_blocked.id}, {opp_pass.id}"
        quote_ids = ", ".join(str(qid) for qid in created_quote_ids)
        for sql in (
            f"delete from opportunity_stage_history where opportunity_id in ({opp_ids})",
            "delete from audit_logs where business_type = 'opportunity' "
            f"and business_id in ({opp_ids})",
            f"delete from quote_versions where quote_id in ({quote_ids})",
            f"delete from quotes where id in ({quote_ids})",
            f"delete from opportunities where id in ({opp_ids})",
            f"delete from customers where id = {customer.id}",
        ):
            await session.execute(text(sql))
        await session.commit()


async def check_receivable_due_reminder() -> None:
    """⑥ 应收到期提醒的"今天"必须按北京时间（第十二批 12.7）。

    修复前这里用的是 `datetime.now(UTC).date()` —— 北京时间凌晨 0-8 点会比业务日早
    一天，而**默认的自动任务调度正好是凌晨跑**：当天到期的提醒要等到第二天才发得出来。

    "提前 0 天"这条规则最能暴露它：到期日就是今天时，凌晨那一扫必须命中。
    """
    from sqlalchemy import func

    from app.modules.order.model import SalesOrder
    from app.modules.payment.model import ReceivablePlan
    from app.modules.settings import service as settings_service
    from app.modules.settings.model import TaskRule
    from app.modules.task.model import Task
    from app.modules.user.model import User

    async with SessionLocal() as session:
        admin = (await session.execute(
            select(User).where(User.username == "admin"))).scalar_one()
        customer = Customer(name=f"{PREFIX}应收提醒客户", owner_id=admin.id, created_by=admin.id)
        session.add(customer)
        await session.flush()
        order = SalesOrder(order_no=f"{PREFIX}SO-DUE", customer_id=customer.id,
                           owner_id=admin.id, status="confirmed",
                           total_amount=Decimal("100"), currency="CNY", created_by=admin.id)
        session.add(order)
        await session.flush()
        session.add(ReceivablePlan(order_id=order.id, plan_name=f"{PREFIX}应收-元旦",
                                   due_date=date(2026, 1, 1), amount=Decimal("100"),
                                   status="pending", created_at=datetime.now(UTC)))
        rule = TaskRule(code=f"{PREFIX}_due0", name=f"{PREFIX}到期当天提醒",
                        trigger_type="receivable_due", trigger_config={"days": 0},
                        action_config={"title": f"{PREFIX}应收到期"}, status="active")
        session.add(rule)
        await session.flush()

        async def scan_and_count() -> int:
            await settings_service.run_auto_tasks(session, operator_id=None, source="CHECK")
            return int((await session.execute(
                select(func.count(Task.id)).where(Task.source_rule_id == rule.id)
            )).scalar_one())

        @contextmanager
        def frozen_both(instant):
            """本节点专用：**同时**钉死 timebase 与 settings.service 两处的 datetime。

            `frozen_business_clock` 只替 timebase —— 那是**修复后**代码读的地方。
            而这一节要能真的验证"修复前"的错，就得把修复前读的那个名字
            （`settings.service` 里 import 进来的 `datetime`）也一起冻上：
            否则旧代码按真实时间跑，断言会"因为别的原因"红或绿 —— 白验（实测踩到）。
            """
            with frozen_business_clock(instant):
                original = settings_service.datetime
                settings_service.datetime = timebase.datetime
                try:
                    yield
                finally:
                    settings_service.datetime = original

        try:
            with frozen_both(beijing(2025, 12, 31, 23)):
                n_before = await scan_and_count()
            check("到期日前一天（北京 12-31 23:00）不提前发", n_before, 0)

            with frozen_both(beijing(2026, 1, 1, 1)):
                n_1am = await scan_and_count()
            check("北京 01:00（UTC 还停在 12-31）也算「当天到期」→ 生成", n_1am, 1)

            with frozen_both(beijing(2026, 1, 1, 9)):
                n_9am = await scan_and_count()
            check("同一天的白天再扫一次不会重复生成", n_9am, 1)
        finally:
            for sql in (
                f"delete from tasks where source_rule_id = {rule.id}",
                f"delete from task_rules where id = {rule.id}",
                f"delete from receivable_plans where order_id = {order.id}",
                f"delete from sales_orders where id = {order.id}",
                f"delete from customers where id = {customer.id}",
            ):
                await session.execute(text(sql))
            await session.commit()


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

    print("\n⑤ 商机两个成交入口的有效期判断")
    await check_opportunity_win_entry_boundary()

    print("\n⑥ 应收到期提醒的「今天」")
    await check_receivable_due_reminder()

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
