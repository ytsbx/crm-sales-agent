from types import SimpleNamespace

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
