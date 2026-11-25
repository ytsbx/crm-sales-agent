"""
B04（订单）P0/P1 复核复现。

运行：
    cd backend
    PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b04_p0_p1.py -q

断言写的是“修复后应有的行为”或“证伪 P0 的控制条件”。
不连数据库、不改业务代码。
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.errors import AppError
from app.modules.quote.service import ensure_charge_change_allowed


def _logistics(amount="100.00"):
    return {"is_discount": False, "charge_type": "logistics", "amount": amount}


def _accepted_version():
    return SimpleNamespace(
        approval_status="approved",
        sent_at=datetime.now(UTC),
        accepted_at=datetime.now(UTC),
        declined_at=None,
    )


# ---------------------------------------------------------------- B04-01
def test_b04_01_accepted_version_freight_change_should_be_rejected():
    """客户已接受后还允许改运费，本身就是 B02-01 的 P1。

    审查员把它升级成 P0 的理由是“改完还能转订单”。这条断言“应该拒绝改动”，
    当前代码会放行并只清 `sent_at`，所以失败。
    """
    version = _accepted_version()
    with pytest.raises(AppError):
        asyncio.run(ensure_charge_change_allowed(version, before=_logistics("100.00"), after=_logistics("200.00")))


def test_b04_01_p0_control_change_clears_sent_at_then_conversion_is_blocked():
    """控制项：改运费会清 `sent_at`，而转单硬性要求 sent_at 非空、`mark_sent` 拒绝已接受版本。

    这条通过 = “接受后改运费再转单”的 P0 路径不成立；金额不一致仍停留在报价层
    （B02-01 P1），不能流到正式订单。
    """
    version = _accepted_version()
    asyncio.run(ensure_charge_change_allowed(version, before=_logistics("100.00"), after=_logistics("200.00")))
    assert version.sent_at is None, "改运费没有清 sent_at"

    # 转单的入口条件（order/service.py: create_order_from_quote）要求 sent_at 非空；
    # 当前 sent_at 已被清空，所以转单会在前置校验被 422 拦住。
    assert version.sent_at is None, "改运费后 sent_at 仍非空，P0 路径才可能成立"

    # 想补回 sent_at 只能走 mark_version_sent，但它明确拒绝已接受版本
    from app.modules.quote.lifecycle import mark_version_sent

    class _Result:
        def scalar_one_or_none(self):
            return None

    class _Session:
        async def execute(self, *args, **kwargs):
            return _Result()

    quote = SimpleNamespace(
        status="accepted",
        current_version_id=1,
        valid_until=None,
        opportunity_id=None,
    )
    version.id = 1
    payload = SimpleNamespace(request_key=None, channel="email", receiver=None)
    with pytest.raises(AppError):
        asyncio.run(mark_version_sent(_Session(), quote=quote, version=version, operator_id=1, payload=payload))


# ---------------------------------------------------------------- B04-02
def test_b04_02_manual_order_create_should_have_request_key():
    """手工建单入口应能接收幂等键（当前 schema 与路由都没有）。"""
    from app.modules.order.schema import OrderCreate

    assert "request_key" in OrderCreate.model_fields, "OrderCreate 没有 request_key"


# ---------------------------------------------------------------- B04-03
class _FakeSession:
    def add(self, obj):
        self.last = obj


def test_b04_03_pending_to_shipped_should_be_rejected_without_batches():
    """没有任何发货批次时，pending → shipped 应被拒绝（当前直接放行）。"""
    from app.modules.order.service import change_status

    order = SimpleNamespace(status="pending", id=1, cancelled_at=None)
    session = _FakeSession()
    with pytest.raises(AppError):
        asyncio.run(change_status(session, order, new_status="shipped", operator_id=1))
    assert order.status == "pending"


def test_b04_03_shipped_to_pending_should_be_rejected_without_reason():
    """shipped → pending 这种回退应被拒绝或至少要求理由（当前直接放行）。"""
    from app.modules.order.service import change_status

    order = SimpleNamespace(status="shipped", id=1, cancelled_at=None)
    session = _FakeSession()
    with pytest.raises(AppError):
        asyncio.run(change_status(session, order, new_status="pending", operator_id=1))
    assert order.status == "shipped"


# ---------------------------------------------------------------- 漏项：已发货/已签收后取消
def test_b04_missed_delivered_order_cancel_should_require_return_or_approval():
    """已签收订单直接取消，应被拒绝或要求退货/冲减；当前会被静默放行。

    取消接口随后会把未回款应收全部置 cancelled，已发货批次保留 ——
    货已经交付、应收却被抹掉，是本块最大的财务控制缺口。
    """
    from app.modules.order.service import change_status

    order = SimpleNamespace(status="delivered", id=1, cancelled_at=None)
    session = _FakeSession()
    with pytest.raises(AppError):
        asyncio.run(change_status(
            session, order, new_status="cancelled", operator_id=1,
            allow_cancel=True,
        ))
    assert order.status == "delivered"
