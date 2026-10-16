"""定制询价改归属：客户与商机必须落在**同一个**客户上。

守 2026-10-16 修的一条：`update_inquiry` 原先把 `validate_links` 的**返回值丢掉**，
改用请求里的原值：

    data["customer_id"] = await svc.validate_links(
        session, user, customer_id=data.get("customer_id", inquiry.customer_id), …)

`validate_links` 会从商机推导出**权威的** customer_id 并 return 它，而上面那行
回读的是请求值。请求显式带 `customer_id: null` 时
（`model_dump(exclude_unset=True)` 会保留这个显式 null），推导出来的正确客户被
覆盖成 None → 需求存下"商机属于客户 B、customer_id 为 NULL"。

为什么这不是小事：NULL 客户会落进 `apply_scope` 的"无客户 ⇒ 同事可见"分支
（`inquiry/service.py`），一条本只该归 A 负责人的需求变成全公司 `quote:view` 可见；
同时转打样会被 `sample/sources.py` 判"来源商机与客户不一致"拒掉、转报价也走不通。
"""
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.core.errors import AppError
from app.modules.inquiry import router as routes
from app.modules.inquiry.schema import CustomInquiryUpdate

_REQUEST = Request({"type": "http", "headers": []})
_USER = Obj(id=1)


class _Result:
    """`session.execute()` 的替身：`.scalars().all()` 是**同步**的（真 SQLAlchemy 也如此）。"""

    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return Obj(all=lambda: list(self._rows))


def _session(rows=()):
    return Obj(
        execute=AsyncMock(return_value=_Result(rows)),
        refresh=AsyncMock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
    )


def _inquiry(**over):
    row = Obj(id=7, root_id=None, deleted_at=None, superseded_at=None, extra={},
              customer_id=None, opportunity_id=None, contact_id=None)
    for key, value in over.items():
        setattr(row, key, value)
    return row


def _wire(monkeypatch, row, validated):
    """把 get_visible_or_404 / validate_links / serialize / 审计都换成替身。"""
    monkeypatch.setattr(routes.svc, "get_visible_or_404", AsyncMock(return_value=row))
    monkeypatch.setattr(routes.svc, "validate_links", AsyncMock(return_value=validated))
    monkeypatch.setattr(routes.svc, "serialize", lambda value: {"customer_id": value.customer_id})
    monkeypatch.setattr(routes, "write_audit", AsyncMock())


@pytest.mark.anyio
async def test_explicit_null_customer_keeps_the_one_derived_from_the_opportunity(monkeypatch):
    """回归：请求带 `customer_id: null` + 商机时，落库的必须是商机推导出的客户。

    修之前这条会存成 None（全公司可见），也就是审查里那条 H1。
    """
    row = _inquiry(customer_id=9, opportunity_id=42)
    _wire(monkeypatch, row, validated=9)

    await routes.update_inquiry(
        7, CustomInquiryUpdate(customer_id=None, opportunity_id=42), _REQUEST, _USER,
        _session([row]),
    )

    assert row.customer_id == 9


@pytest.mark.anyio
async def test_validated_customer_wins_over_the_request_value(monkeypatch):
    """校验函数的返回值是权威值，不能回读请求里的原值。"""
    row = _inquiry(customer_id=None, opportunity_id=None)
    _wire(monkeypatch, row, validated=77)

    await routes.update_inquiry(
        7, CustomInquiryUpdate(opportunity_id=42), _REQUEST, _USER, _session([row])
    )

    assert row.customer_id == 77


@pytest.mark.anyio
async def test_opportunity_without_any_customer_is_rejected(monkeypatch):
    """指定了商机却一个客户都给不出来：明确报错，不静默存 NULL。"""
    row = _inquiry(customer_id=None, opportunity_id=None)
    _wire(monkeypatch, row, validated=None)

    with pytest.raises(AppError) as caught:
        await routes.update_inquiry(
            7, CustomInquiryUpdate(customer_id=None, opportunity_id=42), _REQUEST, _USER, _session()
        )

    assert "客户" in caught.value.message


@pytest.mark.anyio
async def test_clearing_the_opportunity_keeps_the_existing_customer(monkeypatch):
    """只清商机（不动客户）时，客户字段沿用需求上的现值，不能被写成 NULL。"""
    row = _inquiry(customer_id=9, opportunity_id=42)
    _wire(monkeypatch, row, validated=9)

    await routes.update_inquiry(
        7, CustomInquiryUpdate(opportunity_id=None), _REQUEST, _USER, _session([row])
    )

    assert row.customer_id == 9
