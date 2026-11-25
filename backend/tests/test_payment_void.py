"""已确认回款作废（2026-11-25 主人拍板 A 方案）的隔离回归。

守的四件事：
1. 作废只改"算不算已收"，不删记录/分配/凭证；
2. 多期分配全部冲回，应收期状态如实退回（paid → pending/overdue）；
3. 只有 confirmed 能作废；重复作废幂等、不重复写审计；
4. 订单已取消的历史脏数据也能清理（作废后不再有已确认回款）。
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from conftest import make_engine, make_user, SyncSessionAsAsync
from app.core.audit import AuditLog
from app.core.errors import AppError
from app.core.timebase import today_business
from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder
from app.modules.payment.model import PaymentAllocation, PaymentRecord, ReceivablePlan
from app.modules.payment import router
from app.modules.payment.schema import PaymentVoidRequest
from app.modules.payment import service as svc

NOW = datetime.now(UTC)
REQ = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})
USER = make_user(1, data_scope="all", permissions=("payment:view", "payment:manage"))
OTHER_USER = make_user(2, data_scope="all", permissions=("payment:view", "payment:manage"))


class AsyncSession(SyncSessionAsAsync):
    async def delete(self, obj):
        self._session.delete(obj)

    async def refresh(self, obj, **kwargs):
        self._session.refresh(obj, **kwargs)


@pytest.fixture
def case(monkeypatch):
    # 通知/跟进不是本用例的断言对象：只守账与审计，避免为它拖进通知表。
    monkeypatch.setattr(
        "app.modules.followup.service.record_and_notify", AsyncMock()
    )
    monkeypatch.setattr(router.notification_service, "notify", AsyncMock())
    monkeypatch.setattr(router.notification_service, "dispatch_after_commit", AsyncMock())

    engine = make_engine()
    AuditLog.__table__.create(engine, checkfirst=True)
    with engine.connect() as conn:
        conn.connection.create_function("timezone", 2, lambda tz, val: val)
        conn.connection.create_function("to_char", 2, lambda val, fmt: val[:7] if val else None)
    with Session(engine, expire_on_commit=False) as raw:
        raw.add(Customer(id=1, name="作废测试客户", owner_id=1))
        raw.add(
            SalesOrder(
                id=1,
                customer_id=1,
                owner_id=1,
                order_no="VOID-1",
                total_amount=D("200"),
                status="pending",
            )
        )
        raw.add_all(
            [
                ReceivablePlan(
                    id=1,
                    order_id=1,
                    plan_name="第一期",
                    due_date=today_business() + timedelta(days=10),
                    amount=D("100"),
                    status="paid",
                    created_at=NOW,
                ),
                ReceivablePlan(
                    id=2,
                    order_id=1,
                    plan_name="第二期",
                    due_date=today_business() + timedelta(days=30),
                    amount=D("100"),
                    status="pending",
                    created_at=NOW,
                ),
            ]
        )
        payment = PaymentRecord(
            id=1,
            order_id=1,
            receivable_plan_id=1,
            received_date=today_business(),
            received_amount=D("100"),
            status="confirmed",
            allocation_mode="auto",
            confirmed_by=1,
            confirmed_at=NOW,
            voucher_file_id=None,
            voucher_note="银行流水 A",
            created_at=NOW,
        )
        raw.add(payment)
        raw.add(
            PaymentAllocation(
                payment_id=1,
                plan_id=1,
                amount=D("100"),
                source="auto",
                created_by=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        raw.commit()
        yield raw, AsyncSession(raw), payment
    engine.dispose()


@pytest.mark.anyio
async def test_void_reverses_plan_and_keeps_original_allocation(case):
    raw, session, payment = case
    result = await router.void_payment(
        payment.id, PaymentVoidRequest(reason="客户退单，财务核实"), REQ, USER, session
    )
    assert result["code"] == 0
    record = raw.get(PaymentRecord, payment.id)
    assert record.status == "voided"
    assert record.voided_by == 1
    assert record.void_reason == "客户退单，财务核实"
    assert record.confirmed_by == 1  # 原确认事实不覆盖

    # 钱不算已收了，但分配行还在（历史事实不丢）
    assert await svc.confirmed_amount(session, 1) == D("0")
    assert raw.scalar(select(PaymentAllocation.amount).where(PaymentAllocation.payment_id == 1)) == D("100")
    # 序列化仍能列出"原来冲了哪一期"
    item = await svc.serialize_payment(session, record)
    assert item["status"] == "voided"
    assert item["allocations"] and item["allocations"][0]["plan_id"] == 1
    assert item["allocated_amount_exact"] == "0.00"

    # 应收期退回未收
    assert raw.get(ReceivablePlan, 1).status == "pending"
    assert raw.get(ReceivablePlan, 2).status == "pending"
    # 审计有作废动作与原因
    audit = raw.scalar(select(AuditLog).where(AuditLog.action == "void"))
    assert audit is not None
    assert audit.after_data["void_reason"] == "客户退单，财务核实"


@pytest.mark.anyio
async def test_void_multi_period_allocations_all_reverse(case):
    raw, session, payment = case
    # 把 200 分到两期：第一期 100、第二期 100（模拟超收自动抵后续期）
    raw.get(PaymentRecord, 1).received_amount = D("200")
    raw.add(
        PaymentAllocation(
            payment_id=1,
            plan_id=2,
            amount=D("100"),
            source="auto",
            created_by=1,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    raw.get(ReceivablePlan, 2).status = "paid"
    raw.commit()

    await router.void_payment(
        payment.id, PaymentVoidRequest(reason="订单取消，整笔退回"), REQ, USER, session
    )
    assert await svc.confirmed_amount(session, 1) == D("0")
    assert await svc.confirmed_amount(session, 2) == D("0")
    assert raw.get(ReceivablePlan, 1).status == "pending"
    assert raw.get(ReceivablePlan, 2).status == "pending"
    item = await svc.serialize_payment(session, raw.get(PaymentRecord, 1))
    assert len(item["allocations"]) == 2


@pytest.mark.anyio
async def test_void_only_confirmed_and_is_idempotent(case):
    raw, session, payment = case
    # 待确认不能作废
    raw.get(PaymentRecord, 1).status = "pending"
    raw.commit()
    with pytest.raises(AppError) as exc:
        await router.void_payment(payment.id, PaymentVoidRequest(reason="退单"), REQ, USER, session)
    assert "只有已确认" in exc.value.message

    raw.get(PaymentRecord, 1).status = "confirmed"
    raw.commit()
    await router.void_payment(payment.id, PaymentVoidRequest(reason="退单"), REQ, USER, session)
    # 第二次重复作废：成功返回，不重复写审计
    second = await router.void_payment(payment.id, PaymentVoidRequest(reason="再点一次"), REQ, USER, session)
    assert second["code"] == 0
    assert "已经作废" in second["message"]
    audits = raw.scalars(select(AuditLog).where(AuditLog.action == "void")).all()
    assert len(audits) == 1


@pytest.mark.anyio
async def test_void_overdue_plan_falls_back_to_overdue(case):
    raw, session, payment = case
    plan = raw.get(ReceivablePlan, 1)
    plan.due_date = today_business() - timedelta(days=1)
    raw.commit()
    await router.void_payment(payment.id, PaymentVoidRequest(reason="客户退单"), REQ, USER, session)
    assert raw.get(ReceivablePlan, 1).status == "overdue"


@pytest.mark.anyio
async def test_void_legacy_confirmed_payment_on_cancelled_order(case):
    raw, session, payment = case
    order = raw.get(SalesOrder, 1)
    order.status = "cancelled"
    for plan in (raw.get(ReceivablePlan, 1), raw.get(ReceivablePlan, 2)):
        plan.status = "cancelled"
    raw.commit()
    await router.void_payment(
        payment.id, PaymentVoidRequest(reason="历史脏数据清理"), REQ, USER, session
    )
    assert raw.get(PaymentRecord, 1).status == "voided"
    # 已取消的应收计划保持取消，不被重算复活
    assert raw.get(ReceivablePlan, 1).status == "cancelled"
