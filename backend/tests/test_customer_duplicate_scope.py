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
