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
    assert result.before_owners == {"3": 3, "9": 9}


@pytest.mark.anyio
async def test_case_list_customer_filter_matches_both_sides_and_respects_scope(db_session, monkeypatch):
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    from sqlalchemy.schema import CreateTable

    from app.modules.customer.model import Customer, CustomerDuplicateCase
    from tests.conftest import SyncSessionAsAsync

    monkeypatch.setattr(duplicates, "scoped_owner_ids", AsyncMock(return_value=[101]))
    db_session.execute(CreateTable(CustomerDuplicateCase.__table__))
    db_session.add_all([
        Customer(id=customer_id, name=f"客户 {customer_id}", owner_id=owner_id)
        for customer_id, owner_id in [(1, 101), (2, 101), (3, 101), (4, 101), (5, 999)]
    ])
    db_session.flush()
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
