from datetime import UTC, datetime

import pytest

from app.core.errors import AppError
from app.modules.sample import router, service
from app.modules.sample.model import SampleItem, SampleRequest, SampleShipment
from conftest import SyncSessionAsAsync, make_user


@pytest.mark.anyio
async def test_private_revision_does_not_expose_link_on_parent_read_or_write(db_session):
    for model in (SampleRequest, SampleItem, SampleShipment):
        model.__table__.create(db_session.get_bind())
    author = make_user(101, permissions=("sample:view", "sample:manage"), data_scope="all")
    coworker = make_user(102, permissions=("sample:view", "sample:manage"))
    parent = SampleRequest(id=10, owner_id=102, created_by=102, status="shipped", version=1)
    child = SampleRequest(
        id=9011, parent_id=10, owner_id=102, created_by=101, status="pending", version=2,
        source_context={"dingtalk_oa": {"mode": "standalone", "requires_self_oa": True}},
    )
    db_session.add_all([parent, child])
    db_session.commit()
    session = SyncSessionAsAsync(db_session)

    async def normal_list(user, include_history=False):
        return (await router.list_samples(
            keyword=None, status=None, customer_id=None, opportunity_id=None, owner_id=None,
            include_history=include_history, include_deleted=False,
            ship_approval_status=None, page=1, page_size=20,
            user=user, session=session,
        ))["data"]["items"]

    rows = await normal_list(coworker)
    assert [row["id"] for row in rows] == [10]
    assert rows[0]["superseded_by"] is None
    assert rows[0]["revision_locked"] is True
    read = (await router.get_sample(10, coworker, session))["data"]
    assert read["superseded_by"] is None and read["revision_locked"] is True
    with pytest.raises(AppError) as hidden:
        await router.get_sample(child.id, coworker, session)
    assert hidden.value.http_status == 404
    with pytest.raises(AppError) as locked:
        await service.get_visible_or_404(session, coworker, 10, for_update=True, allow_external_flow=True)
    assert str(child.id) not in locked.value.message

    visible = (await router.get_sample(10, author, session))["data"]
    assert visible["superseded_by"] == child.id and visible["revision_locked"] is True

    child.source_context = {"dingtalk_oa": {"mode": "standalone", "submitted": True}}
    db_session.flush()
    assert [row["id"] for row in await normal_list(coworker)] == [child.id]
    assert (await router.get_sample(10, coworker, session))["data"]["superseded_by"] == child.id

    child.deleted_at = datetime.now(UTC)
    db_session.flush()
    historical = (await router.get_sample(10, coworker, session))["data"]
    assert historical["superseded_by"] is None and historical["revision_locked"] is True
