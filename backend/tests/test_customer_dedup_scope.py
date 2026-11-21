from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import AppError
from app.modules.customer import tags_router


@pytest.mark.anyio
async def test_deduplicate_existing_customer_uses_visibility_guard(monkeypatch):
    """查重编辑场景不能读取数据范围外客户的字段。"""

    class Session:
        def __init__(self):
            self.get_called = False

        async def get(self, *args, **kwargs):
            self.get_called = True
            raise AssertionError("deduplicate must use get_visible_customer")

    session = Session()
    user = object()

    async def deny(*args, **kwargs):
        raise AppError(40302, "该客户不在你的数据范围内", 403)

    monkeypatch.setattr(tags_router.svc, "get_visible_customer", deny)

    with pytest.raises(AppError) as exc_info:
        await tags_router.deduplicate({"customer_id": 99}, user=user, session=session)

    assert exc_info.value.http_status == 403
    assert session.get_called is False


@pytest.mark.anyio
async def test_deduplicate_existing_customer_keeps_request_mobile(monkeypatch):
    """现有客户查重使用客户名称，但手机号仍来自请求中的联系人字段。"""

    existing = SimpleNamespace(
        id=99,
        name="客户 99",
        tax_no=None,
        domain=None,
        address=None,
    )
    seen = {}

    async def visible(*args, **kwargs):
        return existing

    async def find(*args, **kwargs):
        seen.update(kwargs)
        return []

    async def scope(*args, **kwargs):
        return [1]

    monkeypatch.setattr(tags_router.svc, "get_visible_customer", visible)
    monkeypatch.setattr(tags_router, "find_duplicate_customers", find)
    monkeypatch.setattr(tags_router, "scoped_owner_ids", scope)

    await tags_router.deduplicate(
        {"customer_id": 99, "mobile": "13800000000"},
        user=object(),
        session=object(),
    )

    assert seen["company_name"] == "客户 99"
    assert seen["mobile"] == "13800000000"
@pytest.mark.anyio
async def test_exact_duplicate_candidates_are_not_crowded_out(db_session, monkeypatch):
    from app.modules.contact_util import find_duplicate_contacts, find_duplicate_customers
    from app.modules.customer.model import Contact, Customer
    from app.modules.settings import service as settings
    from tests.conftest import SyncSessionAsAsync, make_user

    monkeypatch.setattr(settings, "get_setting", AsyncMock(return_value=settings.DEFAULT_SETTINGS["dedup_scoring"]))
    db_session.add_all([
        Customer(id=i, name=f"同名科技{i}", owner_id=999 if i <= 30 else 101)
        for i in range(2, 65)
    ])
    db_session.add(Customer(id=65, name="不同名称", tax_no="EXACT-TAX", owner_id=101))
    db_session.flush()
    db_session.add_all([Contact(id=i, name="张三", customer_id=i) for i in range(2, 65)])
    db_session.add(Contact(id=65, name="李四", customer_id=65, mobile="12345678901", email="same@example.test"))
    db_session.commit()
    session = SyncSessionAsAsync(db_session)
    customers = await find_duplicate_customers(
        session, company_name="同名科技", mobile=None, tax_no="EXACT-TAX", limit=5, owner_ids=[101],
    )
    assert 65 in {row["id"] for row in customers}

    contacts = await find_duplicate_contacts(
        session, user=make_user(), name="张三", email="same@example.test", limit=5,
    )
    assert contacts[0]["id"] == 65
