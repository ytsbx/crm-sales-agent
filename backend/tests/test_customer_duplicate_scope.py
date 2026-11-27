from types import SimpleNamespace

import pytest

from app.modules.customer import duplicates


@pytest.mark.anyio
async def test_resolve_case_rechecks_both_customers_under_row_locks(monkeypatch):
    case = SimpleNamespace(
        id=10,
        customer_id=9,
        candidate_id=3,
        status="pending",
        decision=None,
        resolved_owner_id=None,
        resolved_by=None,
        resolved_at=None,
        remark=None,
        before_owners=None,
    )

    class Result:
        def scalars(self):
            return self

        def first(self):
            return case

    class Session:
        async def execute(self, _statement):
            return Result()

        async def flush(self):
            return None

    calls = []

    async def visible(_session, _actor, customer_id, *, for_update=False):
        calls.append((customer_id, for_update))
        return SimpleNamespace(id=customer_id, owner_id=customer_id)

    monkeypatch.setattr(
        "app.modules.customer.service.get_visible_customer", visible
    )

    result = await duplicates.resolve_case(
        Session(),
        case=case,
        decision="keep_both",
        owner_id=None,
        remark=None,
        actor=SimpleNamespace(id=1),
    )

    assert result.status == "resolved"
    assert calls == [(3, True), (9, True)]
    assert result.before_owners is None


@pytest.mark.anyio
async def test_case_list_customer_filter_matches_both_sides_and_respects_scope(db_session, monkeypatch):
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    from sqlalchemy.schema import CreateTable

    from app.modules.customer.model import Customer, CustomerDuplicateCase
    from tests.conftest import SyncSessionAsAsync, follow_customer

    monkeypatch.setattr(duplicates, "scoped_owner_ids", AsyncMock(return_value=[101]))
    db_session.execute(CreateTable(CustomerDuplicateCase.__table__))
    db_session.add_all([
        Customer(id=customer_id, name=f"客户 {customer_id}", owner_id=owner_id)
        for customer_id, owner_id in [(1, 101), (2, 101), (3, 101), (4, 101), (5, 999)]
    ])
    db_session.flush()
    for customer_id in (1, 2, 3, 4):
        follow_customer(db_session, customer_id, 101)
    db_session.add_all([
        CustomerDuplicateCase(id=case_id, customer_id=left, candidate_id=right,
                              status="pending", created_at=datetime.now(UTC))
        for case_id, left, right in [(11, 1, 2), (12, 3, 1), (13, 3, 4), (14, 1, 5)]
    ])
    db_session.flush()

    session = SyncSessionAsAsync(db_session)
    first, total = await duplicates.list_cases(session, user=object(), customer_id=1, page_size=1)
    second, _ = await duplicates.list_cases(session, user=object(), customer_id=1, page=2, page_size=1)
    assert total == 2
    assert [case["id"] for case in first + second] == [12, 11]


@pytest.mark.anyio
async def test_open_cases_skips_candidate_archived_after_matching(db_session, monkeypatch):
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    from sqlalchemy.schema import CreateTable

    from app.modules import contact_util
    from app.modules.customer.model import Customer, CustomerDuplicateCase
    from tests.conftest import SyncSessionAsAsync

    db_session.execute(CreateTable(CustomerDuplicateCase.__table__))
    customer = Customer(id=1, name="新客户")
    db_session.add_all([customer, Customer(id=2, name="已合并客户", deleted_at=datetime.now(UTC))])
    db_session.flush()
    monkeypatch.setattr(contact_util, "find_duplicate_customers", AsyncMock(return_value=[{"id": 2}]))

    cases = await duplicates.open_cases_for_customer(SyncSessionAsAsync(db_session), customer=customer, source="manual")

    assert cases == []
    assert db_session.query(CustomerDuplicateCase).count() == 0


@pytest.mark.anyio
@pytest.mark.parametrize("mode,requested_tag_ids,expected_after", [
    ("replace", [3], [3]),
    ("add", [3], [1, 2, 3]),
    ("remove", [1], [2]),
])
async def test_batch_tag_audit_preserves_each_customers_before_and_after(
    db_session, mode, requested_tag_ids, expected_after
):
    from fastapi import Request
    from sqlalchemy import select
    from sqlalchemy.schema import CreateTable

    from app.core.audit import AuditLog
    from app.modules.customer.model import Customer, Tag, customer_tags
    from app.modules.customer.tags_router import batch_tag
    from app.modules.customer.tags_schema import CustomerBatchTag
    from tests.conftest import SyncSessionAsAsync, follow_customer, make_user

    db_session.execute(CreateTable(Tag.__table__))
    db_session.execute(CreateTable(customer_tags))
    db_session.execute(CreateTable(AuditLog.__table__))
    db_session.add(Customer(id=1, name="审计样例客户", owner_id=101))
    db_session.flush()
    follow_customer(db_session, 1, 101)
    db_session.add_all([
        Tag(id=1, name="原标签甲"),
        Tag(id=2, name="原标签乙"),
        Tag(id=3, name="新标签丙"),
    ])
    db_session.flush()
    db_session.execute(customer_tags.insert(), [
        {"customer_id": 1, "tag_id": 1},
        {"customer_id": 1, "tag_id": 2},
    ])

    session = SyncSessionAsAsync(db_session)
    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1234)})
    response = await batch_tag(
        CustomerBatchTag(customer_ids=[1], tag_ids=requested_tag_ids, mode=mode),
        request,
        make_user(101),
        session,
    )

    assert response["data"] == {"affected": 1}
    audit = (await session.execute(
        select(AuditLog).where(AuditLog.action == f"batch_tag_{mode}")
    )).scalar_one()
    assert audit.before_data == {"tag_ids_by_customer": {"1": [1, 2]}}
    assert audit.after_data["tag_ids_by_customer"] == {"1": expected_after}
    assert audit.after_data["customer_ids"] == [1]
    assert audit.after_data["tag_ids"] == requested_tag_ids
