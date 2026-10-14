"""第九批复核：物理量真实列边界与分期预览的数据范围。"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from conftest import SyncSessionAsAsync, make_engine, make_user
from app.core.errors import AppError
from app.core.timebase import today_business
from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder
from app.modules.payment.router import preview_receivables
from app.modules.payment.schema import ReceivableGenerate
from app.modules.payment.service import split_amounts
from app.modules.product.master import normalize_field_value
from app.modules.product.schema import SkuCreate


@pytest.mark.parametrize("field,value", [
    ("weight", "1000000000"),  # Numeric(12,3) 只有 9 位整数
    ("carton_volume", "100000000"),  # Numeric(12,4) 只有 8 位整数
    ("carton_qty", "1e30"),
    ("moq", "1e30"),
    ("weight", "NaN"),
    ("weight", "Infinity"),
    ("weight", "-5"),
    ("weight", "1.0001"),
    ("carton_qty", "1.9"),
])
def test_master_rejects_values_that_cannot_be_saved(field, value):
    with pytest.raises(AppError):
        normalize_field_value(field, value)
    with pytest.raises(ValidationError):
        SkuCreate(sku_code="BOUNDARY", **{field: value})


@pytest.mark.parametrize("field,value,expected", [
    ("weight", "999999999.999", Decimal("999999999.999")),
    ("carton_volume", "99999999.9999", Decimal("99999999.9999")),
    ("weight", "7.7700", Decimal("7.77")),
    ("weight", "0.0000", Decimal(0)),
    ("carton_qty", "999999999999", 999999999999),
    ("carton_qty", "1e3", 1000),
])
def test_master_accepts_boundaries_and_equivalent_decimal_values(field, value, expected):
    assert normalize_field_value(field, value) == expected
    # 常规接口的整数入参是 JSON 整数，主数据来源还允许十进制字符串。
    request_value = expected if isinstance(expected, int) else value
    assert getattr(SkuCreate(sku_code="BOUNDARY", **{field: request_value}), field) == expected


def test_half_cent_reference_for_frontend_preview():
    assert split_amounts(Decimal("0.05"), [Decimal("0.7"), Decimal("0.3")]) == [
        Decimal("0.04"), Decimal("0.01"),
    ]


@pytest.mark.anyio
async def test_receivable_preview_checks_scope_and_allows_own_order():
    with Session(make_engine(), expire_on_commit=False) as raw:
        raw.add(Customer(id=1, name="预览客户", owner_id=1))
        for oid, owner in ((1, 1), (2, 2)):
            raw.add(SalesOrder(id=oid, order_no=f"PREVIEW-{oid}", customer_id=1,
                               owner_id=owner, total_amount=100,
                               created_at=datetime.now(UTC)))
        raw.commit()
        session = SyncSessionAsAsync(raw)
        payload = ReceivableGenerate(ratios=[0.3, 0.7], first_due_date=today_business())
        own = make_user(1, permissions=("payment:manage",), data_scope="self")
        result = await preview_receivables(1, payload, own, session)
        assert result["data"]["amounts"] == [30, 70]
        with pytest.raises(AppError, match="数据范围"):
            await preview_receivables(2, payload, own, session)
        all_scope = make_user(1, permissions=("payment:manage",), data_scope="all")
        assert (await preview_receivables(2, payload, all_scope, session))["code"] == 0
