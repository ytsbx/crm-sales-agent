"""第八批 8.3 / 8.4：AI 客户全貌的分块授权、联系方式脱敏，以及应收口径。

守六件事（每件都是修前会 FAIL 的真缺陷）：

1. **工具没有声明模块权限、网关也不检查**（§8.3）：只有 `agent:use` 的人
   能通过 Agent 读财务/成本。修后按 `ToolSpec.permissions` 在**三处执行入口**
   （流式 / 确认卡 / 重试）统一拦截。
2. **客户全貌按 customer_id 全取**（§8.3）：联系人/商机/报价/订单不看模块权限。
   修后无权块**一次查询都不发**，键不出现，只在 `unavailable_blocks` 里说明。
3. **完整手机号直接返回**（§8.3）：修后非授权调用者只看得到脱敏值。
4. **员工编号当订单编号**（§8.4）：`SalesOrder.id.in_(owner_ids)`。用"员工 id
   恰好等于别人订单 id"的反例复现：自己的 100 被漏、别人的 500 被算进来。
5. **取消单/币种直接相加**（§8.4）：修后排除取消订单、按币种分组、Decimal 计算。
6. **范围外 order_id 要有受控响应**（§8.4）：不能返回别人的金额，也不能
   静默回 0（那会被读成"这单没钱"）。

用例用真函数 + 内存 SQLite（见 tests/conftest.py）。
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tests.conftest import SyncSessionAsAsync, make_user

from app.core.errors import AppError, ErrorCode
from app.modules.agent.tools import (
    TOOLS,
    ToolContext,
    ensure_tool_permission,
    get_customer_overview,
    get_receivables_summary,
    missing_permissions,
)
from app.modules.customer.model import Contact, Customer
from app.modules.order.model import SalesOrder
from app.modules.payment.model import PaymentRecord, ReceivablePlan
from app.modules.user.model import User

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def _ctx(session, user, action_id=None):
    return ToolContext(
        session=SyncSessionAsAsync(session), user=user, agent_session_id=1, action_id=action_id
    )


def _customer(session, *, owner_id=101, name="宏远科技"):
    row = Customer(
        name=name,
        level="A",
        status="active",
        pool_status="private",
        owner_id=owner_id,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(row)
    session.flush()
    return row


def _order(session, *, customer_id, owner_id, order_no, total="1000", currency="CNY", status="pending"):
    row = SalesOrder(
        order_no=order_no,
        customer_id=customer_id,
        owner_id=owner_id,
        total_amount=Decimal(total),
        currency=currency,
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(row)
    session.flush()
    return row


def _plan(session, *, order_id, amount="100", currency="CNY", status="pending"):
    row = ReceivablePlan(
        order_id=order_id,
        plan_name="全款",
        due_date=date(2026, 12, 1),
        amount=Decimal(amount),
        currency=currency,
        status=status,
        created_at=NOW,
    )
    session.add(row)
    session.flush()
    return row


def _paid(session, *, order_id, plan_id, amount="40", currency="CNY", status="confirmed"):
    row = PaymentRecord(
        order_id=order_id,
        receivable_plan_id=plan_id,
        received_date=date(2026, 10, 1),
        received_amount=Decimal(amount),
        currency=currency,
        status=status,
        created_at=NOW,
    )
    session.add(row)
    session.flush()
    return row


# ---------------------------------------------------------------- 8.3 工具声明与网关


def test_every_read_tool_declares_its_module_permission():
    """只读工具必须声明对应模块的查看权限，且权限码与模块路由一致。

    这条是"防回归"的：将来新增工具忘了写 `permissions`，
    它就会变成"只有 agent:use 就能用"的越权入口，而这个测试会立刻炸。
    """
    expected = {
        "search_customers": ("customer:view",),
        "get_customer_overview": ("customer:view",),
        "get_contact": ("customer:view",),
        "list_opportunities": ("opportunity:view",),
        "get_opportunity_detail": ("opportunity:view",),
        "search_leads": ("lead:view",),
        "list_sku_options": ("product:view",),
        "get_product": ("product:view",),
        "search_skus": ("product:view",),
        "calculate_price": ("product:view",),
        "calculate_logistics": ("product:view",),
        "get_order": ("order:view",),
        "get_receivables_summary": ("payment:view",),
        "update_opportunity_next_action": ("opportunity:manage",),
        "create_quote_draft": ("quote:manage",),
        "create_quote_version": ("quote:manage",),
        "request_quote_approval": ("quote:manage",),
        "create_followup": ("followup:create",),
        "create_task": ("task:manage",),
    }
    for name, perms in expected.items():
        assert TOOLS[name].permissions == perms, name
    assert all(spec.permissions for spec in TOOLS.values()), "有工具没有声明权限"


def test_gateway_denies_tool_without_module_permission():
    """只有 agent:use 的人拿不到财务汇总：网关按声明的权限码拒绝。"""
    user = make_user(101, permissions={"agent:use"}, roles=["salesperson"], data_scope="self")
    spec = TOOLS["get_receivables_summary"]
    assert missing_permissions(spec, user) == ["payment:view"]
    with pytest.raises(AppError) as exc:
        ensure_tool_permission(spec, user)
    assert exc.value.code == ErrorCode.FORBIDDEN
    assert "payment:view" in exc.value.message

    # 对照：给了权限就放行（不能把功能整个锁死）
    allowed = make_user(
        101, permissions={"agent:use", "payment:view"}, roles=["salesperson"], data_scope="self"
    )
    ensure_tool_permission(spec, allowed)
    # 管理员默认放行（与 require_permission 同一口径）
    ensure_tool_permission(spec, make_user(1, permissions=set(), roles=["admin"], data_scope="all"))


# ---------------------------------------------------------------- 8.3 客户全貌分块


@pytest.mark.anyio
async def test_overview_skips_unauthorised_blocks(db_session):
    """只有 customer:view：商机/报价/订单/应收的键不能出现，且要说明原因。"""
    customer = _customer(db_session)
    order = _order(db_session, customer_id=customer.id, owner_id=101, order_no="SO-1")
    _plan(db_session, order_id=order.id, amount="100")
    db_session.commit()

    user = make_user(
        101, permissions={"customer:view"}, roles=["salesperson"], data_scope="self"
    )
    data = await get_customer_overview(_ctx(db_session, user), customer.id)

    for key in ("opportunities", "quotes", "orders", "pending_receivable_amount"):
        assert key not in data, key
    assert data["unavailable_blocks"] == [
        "商机（需要 opportunity:view）",
        "报价（需要 quote:view）",
        "订单（需要 order:view）",
        "应收与回款（需要 payment:view）",
    ]

    # 对照：权限齐备时各块都在（不能为了修权限把客户全貌弄空）
    full = make_user(
        101,
        permissions={
            "customer:view",
            "opportunity:view",
            "quote:view",
            "order:view",
            "payment:view",
        },
        roles=["salesperson"],
        data_scope="self",
    )
    data_full = await get_customer_overview(_ctx(db_session, full), customer.id)
    assert data_full["orders"][0]["order_no"] == "SO-1"
    assert "unavailable_blocks" not in data_full


@pytest.mark.anyio
async def test_overview_masks_contact_for_non_owner(db_session):
    """非管理员 + 该客户不在本人范围：手机号/邮箱必须脱敏。

    负责人看本客户要能看到完整值（否则没法打电话），所以同时验证两遍。
    """
    customer = _customer(db_session, owner_id=999)
    db_session.add(
        Contact(
            customer_id=customer.id,
            name="张工",
            mobile="13800001111",
            email="zhang@hongyuan.example",
            is_primary=True,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    db_session.commit()

    outsider = make_user(
        101, permissions={"customer:view"}, roles=["boss"], data_scope="all"
    )
    data = await get_customer_overview(_ctx(db_session, outsider), customer.id)
    # 数据范围 all 视为"看得到全部数据"，此时按口径给完整值
    assert data["contacts"][0]["mobile"] == "13800001111"
    assert data["contact_masked"] is False

    owner = make_user(
        999, permissions={"customer:view"}, roles=["salesperson"], data_scope="self"
    )
    data_owner = await get_customer_overview(_ctx(db_session, owner), customer.id)
    assert data_owner["contacts"][0]["mobile"] == "13800001111"

    # 同范围但不是本客户负责人、也不是主管（别人的客户 + self 范围）：
    # 这里用一个 owner 为空（公海）的客户验证保守侧——非管理员一律脱敏
    public = _customer(db_session, owner_id=None, name="公海客户")
    db_session.add(
        Contact(
            customer_id=public.id,
            name="李工",
            mobile="13900002222",
            email="li@example.com",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    db_session.commit()
    data_public = await get_customer_overview(_ctx(db_session, owner), public.id)
    assert data_public["contacts"][0]["mobile"] == "139****2222"
    assert data_public["contacts"][0]["email"] == "l***@example.com"
    assert data_public["contact_masked"] is True
    # 完整值绝不能藏在原始响应里
    assert "13900002222" not in str(data_public)
    assert "li@example.com" not in str(data_public)


@pytest.mark.anyio
async def test_overview_pending_receivable_excludes_cancelled_and_is_decimal(db_session):
    """待回款：排除取消单、Decimal 累加（不出现 0.30000000000000004 这类尾巴）。"""
    customer = _customer(db_session)
    live = _order(db_session, customer_id=customer.id, owner_id=101, order_no="SO-A", total="0.1")
    dead = _order(
        db_session,
        customer_id=customer.id,
        owner_id=101,
        order_no="SO-B",
        total="1000",
        status="cancelled",
    )
    db_session.add_all(
        [
            _plan(db_session, order_id=live.id, amount="0.05"),
            _plan(db_session, order_id=dead.id, amount="999"),
        ]
    )
    db_session.flush()
    db_session.commit()

    user = make_user(
        101,
        permissions={"customer:view", "order:view", "payment:view"},
        roles=["salesperson"],
        data_scope="self",
    )
    data = await get_customer_overview(_ctx(db_session, user), customer.id)
    # 取消单（SO-B）不进待回款；SO-A：0.1 - 0 = 0.1
    assert data["pending_receivable_amount"] == 0.1
    assert data["pending_receivable_currency"] == "CNY"
    assert [o["order_no"] for o in data["orders"]] == ["SO-A", "SO-B"]
    assert "正式应收" in data["receivable_note"]


# ---------------------------------------------------------------- 8.4 应收口径


@pytest.mark.anyio
async def test_receivables_filter_by_order_owner_not_by_order_id(db_session):
    """核心反例：员工 10 自己的订单 700 应收 100；别人订单 id=10 应收 500。

    修前 `SalesOrder.id.in_([10])` 会命中订单 10（别人的 500），漏掉订单 700
    （自己的 100）——两个方向都错。
    """
    customer = _customer(db_session, owner_id=10)
    # 别人的订单，id 恰好等于员工 10 的编号
    other = SalesOrder(
        id=10,
        order_no="SO-OTHER",
        customer_id=customer.id,
        owner_id=20,
        total_amount=Decimal("500"),
        currency="CNY",
        status="pending",
        created_at=NOW,
        updated_at=NOW,
    )
    mine = SalesOrder(
        id=700,
        order_no="SO-MINE",
        customer_id=customer.id,
        owner_id=10,
        total_amount=Decimal("100"),
        currency="CNY",
        status="pending",
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add_all([other, mine])
    db_session.flush()
    _plan(db_session, order_id=10, amount="500")
    _plan(db_session, order_id=700, amount="100")
    db_session.commit()

    # User(id=10) 必须真的存在，`visible_order_ids_stmt` 才能算出范围
    db_session.add(
        User(
            id=10,
            name="业务员十",
            username="u10",
            password_hash="x",
            status="active",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    db_session.commit()

    user = make_user(
        10, permissions={"payment:view"}, roles=["salesperson"], data_scope="self"
    )
    data = await get_receivables_summary(_ctx(db_session, user))

    assert data["plan_count"] == 1
    assert data["plan_amount"] == 100.0, "只应看到自己订单 700 的 100"
    assert data["currency"] == "CNY"


@pytest.mark.anyio
async def test_receivables_group_by_currency_and_exclude_cancelled(db_session):
    """不同币种分组、取消订单不计、实收按已确认。"""
    customer = _customer(db_session, owner_id=101)
    cny = _order(db_session, customer_id=customer.id, owner_id=101, order_no="SO-CNY", total="1000")
    usd = _order(
        db_session,
        customer_id=customer.id,
        owner_id=101,
        order_no="SO-USD",
        total="1000",
        currency="USD",
    )
    dead = _order(
        db_session,
        customer_id=customer.id,
        owner_id=101,
        order_no="SO-DEAD",
        total="5000",
        status="cancelled",
    )
    plan_cny = _plan(db_session, order_id=cny.id, amount="100", currency="CNY")
    plan_usd = _plan(db_session, order_id=usd.id, amount="100", currency="USD")
    _plan(db_session, order_id=dead.id, amount="5000")
    _paid(db_session, order_id=cny.id, plan_id=plan_cny.id, amount="40", currency="CNY")
    _paid(db_session, order_id=usd.id, plan_id=plan_usd.id, amount="10", currency="USD")
    # 未确认的回款不算
    _paid(db_session, order_id=cny.id, plan_id=plan_cny.id, amount="999", status="pending")
    db_session.commit()

    user = make_user(
        101, permissions={"payment:view"}, roles=["salesperson"], data_scope="self"
    )
    data = await get_receivables_summary(_ctx(db_session, user))

    assert data["currency_count"] == 2
    groups = {row["currency"]: row for row in data["by_currency"]}
    assert groups["CNY"]["plan_amount"] == 100.0 and groups["CNY"]["received_amount"] == 40.0
    assert groups["USD"]["plan_amount"] == 100.0 and groups["USD"]["received_amount"] == 10.0
    # 多币种时不给顶层合计（100 + 100 变成无币种的 200 是错的）
    assert "plan_amount" not in data
    assert data["notice"] == "存在多个币种的应收，已按币种分组；不同币种不做合计"


@pytest.mark.anyio
async def test_receivables_rejects_order_out_of_scope(db_session):
    """范围外 order_id 返回受控的"无权/不可见"，不泄漏金额。"""
    customer = _customer(db_session, owner_id=999)
    other = _order(db_session, customer_id=customer.id, owner_id=999, order_no="SO-X", total="500")
    _plan(db_session, order_id=other.id, amount="500")
    db_session.commit()

    user = make_user(
        101, permissions={"payment:view"}, roles=["salesperson"], data_scope="self"
    )
    with pytest.raises(AppError) as exc:
        await get_receivables_summary(_ctx(db_session, user), other.id)
    assert exc.value.code == ErrorCode.DATA_SCOPE_DENIED
    assert "范围" in exc.value.message


@pytest.mark.anyio
async def test_receivables_cancelled_order_is_explicit(db_session):
    """单指定一张取消单：明确说"不产生正式应收"，不回一堆 0。"""
    customer = _customer(db_session, owner_id=101)
    dead = _order(
        db_session,
        customer_id=customer.id,
        owner_id=101,
        order_no="SO-DEAD",
        total="1000",
        status="cancelled",
    )
    _plan(db_session, order_id=dead.id, amount="1000")
    db_session.commit()

    user = make_user(
        101, permissions={"payment:view"}, roles=["salesperson"], data_scope="self"
    )
    data = await get_receivables_summary(_ctx(db_session, user), dead.id)
    assert data["order_status"] == "cancelled"
    assert data["groups"] == []
    assert "取消" in data["notice"]


@pytest.mark.anyio
async def test_receivables_overdue_uses_decimal_remaining(db_session):
    """逾期节点：未收金额 = 计划额 − 已确认回款（Decimal，不跨币种相减）。"""
    customer = _customer(db_session, owner_id=101)
    order = _order(db_session, customer_id=customer.id, owner_id=101, order_no="SO-OV", total="300")
    plan = _plan(db_session, order_id=order.id, amount="100.5", status="overdue")
    _paid(db_session, order_id=order.id, plan_id=plan.id, amount="0.25")
    db_session.commit()

    user = make_user(
        101, permissions={"payment:view"}, roles=["salesperson"], data_scope="self"
    )
    data = await get_receivables_summary(_ctx(db_session, user))
    assert data["overdue_count"] == 1
    assert data["overdue"][0]["remaining_amount"] == 100.25
    assert data["overdue"][0]["currency"] == "CNY"
