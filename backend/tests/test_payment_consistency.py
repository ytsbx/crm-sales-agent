"""第七批 7.9：回款录入、应收生成与并发确认的一致性保护（离线可判定部分）。

真并发（两个 50 同时确认、两个请求同时生成计划）只能在真 PostgreSQL 上验，
见 `backend/scripts/check_payment_consistency.py`。这里守的是离线能钉死的三组：

1. **请求键**：同一把键重复提交只建一行并回放第一次的结果；**不同的键要
   保留两行** —— 同额同日的两笔真实回款不能被"金额+日期相同"这种猜测合并掉
   （这是本条最容易修过头的方向，所以它和幂等一起断言）。
2. **币种**：从应收节点继承，不再落到列默认的 CNY；给了不一致的币种要拒绝。
3. **锁的接线**：确认/改回款必须先锁共同应收节点再锁回款记录，且"汇总已确认
   金额"的查询发生在拿到节点锁之后；生成计划必须先锁订单行再做"查无再插"；
   删/改节点也要在锁内做判断。

第 3 组断言的是**真 SQL 语句的先后**（内存 SQLite 会忽略 `FOR UPDATE`，但语句
照样生成），所以它挡的是"将来有人把锁去掉、换错顺序、或把汇总挪到锁外面"，
而不是"SQLite 上能不能真并发"——后者属于真库脚本。
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from starlette.requests import Request

from tests.conftest import SyncSessionAsAsync, make_user

from app.core.audit import AuditLog
from app.core.base import Base
from app.core.errors import AppError, ErrorCode
from app.core.idempotency import RequestKey
from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder
from app.modules.payment import service as payment_service
from app.modules.payment.model import PaymentRecord, ReceivablePlan
from app.modules.payment.router import (
    confirm_payment,
    create_payment,
    delete_receivable,
    generate_receivables,
    update_payment,
    update_receivable,
)
from app.modules.payment.schema import (
    PaymentAction,
    PaymentCreate,
    PaymentUpdate,
    ReceivableGenerate,
    ReceivableUpdate,
)

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
DAY = date(2026, 10, 1)


# ------------------------------------------------------------------ 夹具与替身


class PaymentSession(SyncSessionAsAsync):
    """测试会话：记录 `execute` 到的 SQL 顺序，并补上替身缺的 `delete/rollback`。

    `SyncSessionAsAsync` 的 `execute` 已经转给真 SQLAlchemy 执行，这里只包一层
    记录：断言的是**真实生成的 SQL**（含 `FOR UPDATE`），不是"我以为它会这么写"。
    `delete` 是回款/幂等释放要用的（删占位、删节点）；
    `rollback` 是幂等占位撞唯一约束后的重判要用的。
    """

    def __init__(self, session):
        super().__init__(session)
        self.statements: list[str] = []

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(str(stmt))
        return await super().execute(stmt, *args, **kwargs)

    async def delete(self, obj):
        # 路由里是 `await session.delete(...)`（真 AsyncSession 的 delete 就是协程），
        # 所以替身这里也必须是协程，不能只转成同步调用。
        return self._session.delete(obj)

    async def rollback(self):
        self._session.rollback()


@pytest.fixture()
def pay_db(db_session):
    """内存库里补建两张被替身漏掉的表：审计与通用请求键。"""
    Base.metadata.create_all(
        db_session.get_bind(),
        tables=[RequestKey.__table__, AuditLog.__table__],
        checkfirst=True,
    )
    return db_session


@pytest.fixture()
def pay(pay_db):
    """给被测路由/服务函数用的异步会话替身（同步库 `pay_db` 仍用于直接断言）。"""
    return PaymentSession(pay_db)


@pytest.fixture()
def quiet_notifications(monkeypatch):
    """确认回款会顺带写跟进留痕与通知（followups/business_events/notifications 表）。

    本文件测的是锁与币种，不是通知内容，所以把这两处副作用换成空实现；
    通知与留痕本身的正确性由 followup / notification 各自的套件守。
    """
    calls: list[tuple] = []

    async def _noop(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr("app.modules.followup.service.record_and_notify", _noop)
    monkeypatch.setattr("app.modules.notification.service.notify", _noop)
    monkeypatch.setattr("app.modules.notification.service.dispatch_pending", _noop)
    return calls


def make_request(request_key: str | None = None) -> Request:
    """最小可用 Request：`client_ip` 读 x-forwarded-for / client.host。"""
    headers = []
    if request_key is not None:
        headers.append((b"x-request-key", request_key.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/payments",
            "headers": headers,
            "client": ("127.0.0.1", 34567),
            "query_string": b"",
        }
    )


def operator(user_id: int = 101):
    """scope=all 的操作者：本文件不测数据范围（已有专门用例），只要放行。"""
    return make_user(user_id, permissions={"payment:manage"}, roles=["admin"], data_scope="all")


def make_order(session, *, total="100", currency="CNY", owner_id=101):
    customer = Customer(
        name=f"回款一致性客户{total}",
        level="B",
        status="active",
        pool_status="private",
        owner_id=owner_id,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(customer)
    session.flush()
    order = SalesOrder(
        order_no=f"SO-PAY-{customer.id}",
        customer_id=customer.id,
        owner_id=owner_id,
        total_amount=Decimal(total),
        currency=currency,
        status="pending",
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(order)
    session.flush()
    return order


def make_plan(session, order, *, amount="100", currency=None, name="全款"):
    plan = ReceivablePlan(
        order_id=order.id,
        plan_name=name,
        due_date=date(2026, 12, 1),
        amount=Decimal(amount),
        currency=currency or order.currency,
        status="pending",
        created_at=NOW,
    )
    session.add(plan)
    session.flush()
    return plan


def make_payment(session, plan, *, amount="50", status="pending", currency=None):
    record = PaymentRecord(
        receivable_plan_id=plan.id,
        order_id=plan.order_id,
        received_date=DAY,
        received_amount=Decimal(amount),
        currency=currency or plan.currency,
        status=status,
        created_at=NOW,
    )
    session.add(record)
    session.flush()
    return record


def indexes(statements: list[str], *needles: str) -> list[int]:
    """语句序列里同时含全部 needle 的下标。"""
    return [
        index
        for index, sql in enumerate(statements)
        if all(needle.lower() in sql.lower() for needle in needles)
    ]


def lock_indexes(statements: list[str], table: str) -> list[int]:
    """某张表上的 `SELECT ... FOR UPDATE` 语句下标。"""
    return indexes(statements, f"from {table}", "for update")


def create_payload(plan, **overrides) -> PaymentCreate:
    body = {
        "receivable_plan_id": plan.id,
        "received_date": DAY,
        "received_amount": 50,
        "payment_method": "银行转账",
    }
    body.update(overrides)
    return PaymentCreate(**body)


# ------------------------------------------------------ 1. 请求键：一行 vs 两行


@pytest.mark.anyio
async def test_same_request_key_creates_one_row_and_replays(pay_db, pay):
    """弱网重试：同一把键重复提交 → 库里一行，第二次返回的是**同一条**记录。

    修前没有任何请求键：两次 POST 就是两条回款，财务看到两笔 50，
    节点被多确认 50 —— 这正是 7.9 要堵的第一条。
    """
    order = make_order(pay_db, total="100")
    plan = make_plan(pay_db, order, amount="100")
    pay_db.commit()

    first = await create_payment(
        create_payload(plan, request_key="weak-net-retry-1"),
        make_request("weak-net-retry-1"),
        operator(),
        pay,
    )
    second = await create_payment(
        create_payload(plan, request_key="weak-net-retry-1"),
        make_request("weak-net-retry-1"),
        operator(),
        pay,
    )

    assert second["data"]["id"] == first["data"]["id"], "同键重复提交必须回放原记录"
    assert "此前已登记" in second["message"]
    rows = pay_db.query(PaymentRecord).filter(PaymentRecord.receivable_plan_id == plan.id).all()
    assert len(rows) == 1
    # 回放的是**存下来**的响应体（JSON 列里 date 会变成 ISO 串），
    # 存的时候必须用 JSON 安全形态，否则 flush 直接 TypeError（500）。
    assert isinstance(second["data"]["received_date"], str)
    assert second["data"]["received_amount"] == 50.0


@pytest.mark.anyio
async def test_same_amount_same_day_with_different_keys_keeps_two_rows(pay_db, pay):
    """同额同日的两笔真实回款，键不同 → 必须保留两行（不能按金额+日期合并）。"""
    order = make_order(pay_db, total="1000")
    plan = make_plan(pay_db, order, amount="1000")
    pay_db.commit()

    for key in ("real-1", "real-2"):
        await create_payment(create_payload(plan, request_key=key), make_request(key), operator(), pay)

    rows = pay_db.query(PaymentRecord).filter(PaymentRecord.receivable_plan_id == plan.id).all()
    assert len(rows) == 2
    assert {row.status for row in rows} == {"pending"}


@pytest.mark.anyio
async def test_create_without_request_key_still_works_but_says_so(pay_db, pay):
    """不带键照旧登记（兼容旧前端/脚本），但响应要说明这次没有幂等保护。"""
    order = make_order(pay_db, total="100")
    plan = make_plan(pay_db, order, amount="100")
    pay_db.commit()

    result = await create_payment(create_payload(plan), make_request(), operator(), pay)

    assert result["code"] == 0
    assert "未带请求键" in result["message"]
    assert pay_db.query(PaymentRecord).count() == 1


@pytest.mark.anyio
async def test_same_request_key_different_content_conflicts(pay_db, pay):
    """同一把键换了金额：报冲突，不许"悄悄按新内容再建一条"。"""
    order = make_order(pay_db, total="100")
    plan = make_plan(pay_db, order, amount="100")
    pay_db.commit()

    await create_payment(
        create_payload(plan, request_key="same-key", received_amount=50),
        make_request("same-key"),
        operator(),
        pay,
    )
    with pytest.raises(AppError) as exc:
        await create_payment(
            create_payload(plan, request_key="same-key", received_amount=30),
            make_request("same-key"),
            operator(),
            pay,
        )
    assert exc.value.code == ErrorCode.VERSION_CONFLICT
    assert pay_db.query(PaymentRecord).count() == 1


# ------------------------------------------------------------------ 2. 币种


@pytest.mark.anyio
async def test_payment_inherits_plan_currency(pay_db, pay):
    """美元节点上的回款必须是美元：修前不传币种会落到列默认的 CNY。"""
    order = make_order(pay_db, total="1000", currency="USD")
    plan = make_plan(pay_db, order, amount="1000", currency="USD")
    pay_db.commit()

    result = await create_payment(create_payload(plan), make_request(), operator(), pay)

    assert result["data"]["currency"] == "USD"
    assert pay_db.get(PaymentRecord, result["data"]["id"]).currency == "USD"


@pytest.mark.anyio
async def test_payment_currency_mismatch_is_rejected(pay_db, pay):
    """与节点币种不一致：明确拒绝（不猜汇率、不改记成节点币种）。"""
    order = make_order(pay_db, total="1000", currency="USD")
    plan = make_plan(pay_db, order, amount="1000", currency="USD")
    pay_db.commit()

    with pytest.raises(AppError) as exc:
        await create_payment(create_payload(plan, currency="CNY"), make_request(), operator(), pay)
    assert exc.value.code == ErrorCode.PARAM_ERROR
    assert "跨币种" in exc.value.message
    assert pay_db.query(PaymentRecord).count() == 0, "拒绝时不能留下半条记录"


@pytest.mark.anyio
async def test_update_payment_currency_must_match_plan(pay_db, pay):
    """改回款时也一样：与节点币种一致才接受。"""
    order = make_order(pay_db, total="100", currency="USD")
    plan = make_plan(pay_db, order, amount="100", currency="USD")
    record = make_payment(pay_db, plan, amount="50")
    pay_db.commit()

    with pytest.raises(AppError) as exc:
        await update_payment(record.id, PaymentUpdate(currency="CNY"), make_request(), operator(), pay)
    assert "跨币种" in exc.value.message

    ok_result = await update_payment(
        record.id, PaymentUpdate(currency="USD"), make_request(), operator(), pay
    )
    assert ok_result["data"]["currency"] == "USD"


@pytest.mark.anyio
async def test_plan_without_currency_is_refused_not_defaulted(pay_db, pay):
    """节点币种为空时不能"顺手记成人民币"，要报出来让业务补齐。"""
    order = make_order(pay_db, total="100")
    plan = make_plan(pay_db, order, amount="100")
    plan.currency = ""
    pay_db.flush()

    with pytest.raises(AppError) as exc:
        await create_payment(create_payload(plan), make_request(), operator(), pay)
    assert exc.value.code == ErrorCode.PARAM_ERROR
    assert "没有币种" in exc.value.message


# ---------------------------------------------------------- 3. 锁的接线与顺序


@pytest.mark.anyio
async def test_confirm_locks_shared_plan_before_payment_record(pay_db, pay, quiet_notifications):
    """确认：先锁共同应收节点，再锁回款记录，最后才汇总。

    修前只有 `payment_records ... FOR UPDATE`：两个各 50 的并发确认锁不到彼此，
    各自读到"已确认 50"写成 partial。顺序错了同样危险 —— 先锁回款再锁节点，
    会和"改节点金额"那条路径的加锁顺序相反，两边各持一把互相等就是死锁。
    """
    order = make_order(pay_db, total="100")
    plan = make_plan(pay_db, order, amount="100")
    first = make_payment(pay_db, plan, amount="50")
    pay_db.commit()

    await confirm_payment(
        first.id, PaymentAction(comment="核对通过"), make_request(), operator(), pay
    )

    plan_locks = lock_indexes(pay.statements, "receivable_plans")
    payment_locks = lock_indexes(pay.statements, "payment_records")
    sums = indexes(pay.statements, "sum(payment_records.received_amount)")

    assert plan_locks, "确认必须先锁共同应收节点（修前完全没有这把锁）"
    assert payment_locks, "回款记录本身仍要锁"
    assert plan_locks[0] < payment_locks[0], "统一锁序：应收节点在回款记录之前"
    assert sums and sums[0] > plan_locks[0], "汇总必须在节点锁内发生，否则读旧数写覆盖"


@pytest.mark.anyio
async def test_two_confirms_on_same_plan_end_paid(pay_db, pay, quiet_notifications):
    """顺序确认两笔 50 → 节点 paid、余额 0（锁 + 重算的正确性左证）。"""
    order = make_order(pay_db, total="100")
    plan = make_plan(pay_db, order, amount="100")
    first = make_payment(pay_db, plan, amount="50")
    second = make_payment(pay_db, plan, amount="50")
    pay_db.commit()

    for payment_id in (first.id, second.id):
        await confirm_payment(payment_id, PaymentAction(), make_request(), operator(), pay)

    pay_db.refresh(plan)
    assert plan.status == "paid"
    summary = await payment_service.order_finance_summary(pay, order.id)
    assert summary["received_amount"] == 100.0
    assert summary["unreceived_amount"] == 0.0


@pytest.mark.anyio
async def test_confirm_and_reject_race_leaves_one_terminal_state(pay_db, pay, quiet_notifications):
    """同一笔回款先确认再驳回：第二次必须被挡（终态不能被覆盖）。"""
    order = make_order(pay_db, total="100")
    plan = make_plan(pay_db, order, amount="100")
    record = make_payment(pay_db, plan, amount="100")
    pay_db.commit()

    await confirm_payment(record.id, PaymentAction(), make_request(), operator(), pay)
    with pytest.raises(AppError) as exc:
        await confirm_payment(record.id, PaymentAction(), make_request(), operator(), pay)
    assert exc.value.code == ErrorCode.STATUS_NOT_ALLOWED

    pay_db.refresh(record)
    assert record.status == "confirmed"
    pay_db.refresh(plan)
    assert plan.status == "paid"


@pytest.mark.anyio
async def test_update_payment_amount_recalcs_inside_plan_lock(pay_db, pay):
    """改回款金额：也要先锁节点再锁记录，并在锁内重算（修前重算在锁外）。"""
    order = make_order(pay_db, total="100")
    plan = make_plan(pay_db, order, amount="100")
    record = make_payment(pay_db, plan, amount="50")
    pay_db.commit()

    await update_payment(
        record.id, PaymentUpdate(received_amount=100), make_request(), operator(), pay
    )

    plan_locks = lock_indexes(pay.statements, "receivable_plans")
    payment_locks = lock_indexes(pay.statements, "payment_records")
    sums = indexes(pay.statements, "sum(payment_records.received_amount)")
    assert plan_locks and payment_locks
    assert plan_locks[0] < payment_locks[0]
    assert sums and sums[0] > plan_locks[0]


@pytest.mark.anyio
async def test_generate_locks_order_before_checking_existing_plans(pay_db, pay):
    """生成计划：先锁订单行，再查"有没有计划"。

    修前是裸的"先查无再插"：两个并发生成都能查到"没有"，各插一套。
    这里断言订单行锁出现在那次存在性查询之前。
    """
    order = make_order(pay_db, total="1000")
    pay_db.commit()

    result = await generate_receivables(
        order.id,
        ReceivableGenerate(ratios=[0.3, 0.7], first_due_date=date(2026, 12, 1)),
        make_request(),
        operator(),
        pay,
    )

    assert result["code"] == 0
    order_locks = lock_indexes(pay.statements, "sales_orders")
    existing_checks = indexes(pay.statements, "select receivable_plans.id")
    assert order_locks, "生成计划必须先锁共同订单"
    assert existing_checks and order_locks[0] < existing_checks[0], "锁要在查无之前拿到"


@pytest.mark.anyio
async def test_generate_with_request_key_replays_instead_of_conflict(pay_db, pay):
    """双击"生成计划"：同一把键第二次回放第一次的结果，而不是报"已有计划"。"""
    order = make_order(pay_db, total="1000")
    pay_db.commit()
    payload = ReceivableGenerate(
        ratios=[0.5, 0.5], first_due_date=date(2026, 12, 1), request_key="gen-1"
    )

    first = await generate_receivables(order.id, payload, make_request(), operator(), pay)
    second = await generate_receivables(order.id, payload, make_request(), operator(), pay)

    assert [row["id"] for row in second["data"]] == [row["id"] for row in first["data"]]
    assert pay_db.query(ReceivablePlan).filter(ReceivablePlan.order_id == order.id).count() == 2


@pytest.mark.anyio
async def test_plan_writes_take_the_plan_lock(pay_db, pay):
    """改节点、删节点都要在锁内判断：否则与"正在登记回款"互相踩。"""
    order = make_order(pay_db, total="100")
    plan = make_plan(pay_db, order, amount="100")
    spare = make_plan(pay_db, order, amount="1", name="另一期")
    pay_db.commit()

    await update_receivable(plan.id, ReceivableUpdate(amount=80), make_request(), operator(), pay)
    assert lock_indexes(pay.statements, "receivable_plans"), "改节点金额要先锁节点"

    pay.statements.clear()
    await delete_receivable(spare.id, make_request(), operator(), pay)
    assert lock_indexes(pay.statements, "receivable_plans"), "删节点要先锁节点"


@pytest.mark.anyio
async def test_delete_receivable_refuses_when_payment_exists(pay_db, pay):
    """有回款的节点不能删（这条原有行为必须保住）。"""
    order = make_order(pay_db, total="100")
    plan = make_plan(pay_db, order, amount="100")
    make_payment(pay_db, plan, amount="50")
    pay_db.commit()

    with pytest.raises(AppError) as exc:
        await delete_receivable(plan.id, make_request(), operator(), pay)
    assert exc.value.code == ErrorCode.STATUS_NOT_ALLOWED
    assert pay_db.get(ReceivablePlan, plan.id) is not None


@pytest.mark.anyio
async def test_create_payment_locks_plan_before_insert(pay_db, pay):
    """登记回款：先锁应收节点（防"回款挂到已删节点上"）。

    插入语句走 ORM flush、不经过 `Session.execute`，所以这里只断言"节点锁存在
    且发生在任何回款读写之前"——同步 SQLite 认不出真并发，能钉的就是这道接线。
    """
    order = make_order(pay_db, total="100")
    plan = make_plan(pay_db, order, amount="100")
    pay_db.commit()

    await create_payment(create_payload(plan), make_request(), operator(), pay)

    plan_locks = lock_indexes(pay.statements, "receivable_plans")
    assert plan_locks, "登记回款要先锁应收节点"
    assert plan_locks == [0], f"节点锁必须是第一条语句，实际：{pay.statements}"
    assert pay_db.query(PaymentRecord).filter(PaymentRecord.receivable_plan_id == plan.id).count() == 1
