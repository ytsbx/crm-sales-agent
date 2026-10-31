"""目标对象校验：拒绝不存在编号，保留已有对象及历史停用状态。"""
import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.core.errors import AppError
from app.modules.analytics.router import SalesTargetUpsert
from app.modules.analytics import targets


@pytest.mark.parametrize("field", ["user_id", "department_id"])
@pytest.mark.parametrize("value", [0, -1, 9223372036854775808])
def test_ids_must_fit_positive_bigint(field, value):
    with pytest.raises(ValidationError):
        SalesTargetUpsert(period="2026-01", **{field:value})


@pytest.mark.parametrize("field,label", [("user_id", "业务员"), ("department_id", "部门")])
def test_missing_scope_is_rejected_before_target_writes(field,label):
    async def check():
        session=AsyncMock()
        session.get.return_value=None
        payload={"user_id":None,"department_id":None,field:123456}
        with pytest.raises(AppError) as error:
            await targets.upsert_target(session,user=SimpleNamespace(id=1),period="2026-01",
                                        new_customer_target=0,sales_target=Decimal("1.01"),**payload)
        assert error.value.http_status==404 and label+"不存在" in error.value.message
        session.execute.assert_not_called()
        session.add.assert_not_called()
        session.flush.assert_not_called()
    asyncio.run(check())


@pytest.mark.parametrize("value", [2147483648, 9223372036854775808])
def test_new_customer_target_must_fit_storage(value):
    with pytest.raises(ValidationError):
        SalesTargetUpsert(period="2026-01", new_customer_target=value)


@pytest.mark.parametrize("value", [0, 2147483647])
def test_new_customer_target_storage_boundaries_remain_valid(value):
    assert SalesTargetUpsert(period="2026-01", new_customer_target=value).new_customer_target == value


def test_remark_limit_counts_unicode_characters():
    assert SalesTargetUpsert(period="2026-01", remark="注" * 255).remark == "注" * 255
    with pytest.raises(ValidationError):
        SalesTargetUpsert(period="2026-01", remark="注" * 256)


@pytest.mark.parametrize("value", [None, ""])
def test_empty_remark_remains_valid(value):
    assert SalesTargetUpsert(period="2026-01", remark=value).remark == value
