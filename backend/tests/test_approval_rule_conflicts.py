"""Rule activation checks using disposable, in-memory SQLite."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.approval import rules_engine, rules_router
from app.modules.approval.model import ApprovalRule, ApprovalRuleVersion
from tests.conftest import SyncSessionAsAsync, _register_sqlite_bigint_as_integer


def condition(field, op, value):
    return {'field': field, 'op': op, 'value': value}


def test_boundary_can_be_strictly_excluded():
    low = [condition('gross_margin', 'lte', 22)]
    inclusive = [condition('gross_margin', 'gte', 22)]
    strict = [condition('gross_margin', 'gt', 22)]
    assert rules_engine.conditions_overlap(low, inclusive)
    assert not rules_engine.conditions_overlap(low, strict)
    assert not rules_engine.evaluate_conditions(strict, {'gross_margin': 22})[0]
    assert rules_engine.evaluate_conditions(strict, {'gross_margin': 22.01})[0]
    assert not rules_engine.evaluate_conditions([condition('gross_margin', 'lt', 22)], {'gross_margin': 22})[0]


def test_rules_can_overlap_even_with_different_fields():
    auto = [condition('gross_margin', 'gte', 25), condition('total_amount', 'lte', 100000)]
    express = [condition('gross_margin', 'gt', 22), condition('concession_amount', 'lte', 1000)]
    assert rules_engine.conditions_overlap(auto, express)
    assert not rules_engine.conditions_overlap(auto, [*express, condition('gross_margin', 'lt', 25)])
    assert not rules_engine.conditions_overlap(
        [condition('customer_level', 'in', ['A', 'B'])], [condition('customer_level', 'eq', 'C')],
    )
    assert not rules_engine.conditions_overlap(
        [condition('customer_has_overdue', 'eq', True)], [condition('customer_has_overdue', 'eq', False)],
    )


def test_draft_priority_does_not_reorder_published_rules():
    _register_sqlite_bigint_as_integer()
    engine = create_engine('sqlite://')
    ApprovalRule.__table__.create(engine)
    ApprovalRuleVersion.__table__.create(engine)
    with Session(engine) as raw:
        first = ApprovalRule(name='极速通道', kind='express', priority=20, enabled=True,
                             published_version_no=1, conditions=[condition('gross_margin', 'gte', 22)])
        second = ApprovalRule(name='财务会签', kind='exception_route', priority=30, enabled=True,
                              published_version_no=1, conditions=[condition('gross_margin', 'lte', 22)])
        raw.add_all([first, second])
        raw.flush()
        for rule in (first, second):
            raw.add(ApprovalRuleVersion(rule_id=rule.id, version_no=1, payload=rules_router._snapshot(rule)))
        first.priority = 40
        raw.commit()
        published = asyncio.run(rules_engine.load_active_rules(SyncSessionAsAsync(raw)))
        assert [payload['priority'] for _, payload in published] == [20, 30]
        assert [rule.id for rule, _ in published] == [first.id, second.id]
    engine.dispose()


@pytest.mark.parametrize('operation, verb', [
    ('publish', '发布'), ('publish_disabled', '发布'), ('enable', '生效'),
])
def test_conflicting_rule_cannot_activate(monkeypatch, operation, verb):
    _register_sqlite_bigint_as_integer()
    engine = create_engine('sqlite://')
    ApprovalRule.__table__.create(engine)
    ApprovalRuleVersion.__table__.create(engine)
    audit = AsyncMock()
    monkeypatch.setattr(rules_router, 'write_audit', audit)
    monkeypatch.setattr(rules_router, 'client_ip', lambda _: None)
    with Session(engine) as raw:
        session = SyncSessionAsAsync(raw)
        session.get_bind = raw.get_bind
        active = ApprovalRule(id=1, name='财务会签', kind='exception_route', enabled=True,
                              published_version_no=1, conditions=[condition('gross_margin', 'lte', 22)])
        candidate = ApprovalRule(id=2, name='极速通道', kind='express', enabled=operation == 'publish',
                                 published_version_no=1, conditions=[condition('gross_margin', 'gte', 22)])
        raw.add_all([active, candidate]); raw.flush()
        raw.add_all([ApprovalRuleVersion(rule_id=row.id, version_no=1, payload=rules_router._snapshot(row))
                     for row in (active, candidate)])
        raw.commit()
        user = SimpleNamespace(id=1)

        def activate():
            if operation != 'enable':
                return rules_router.publish_rule(2, None, True, user, session)
            return rules_router.toggle_rule(2, rules_router.RuleToggle(enabled=True), None, user, session)

        with pytest.raises(AppError, match=f'极速通道.*财务会签.*不能{verb}') as error:
            asyncio.run(activate())
        assert error.value.http_status == 409
        assert candidate.enabled is (operation == 'publish')
        assert candidate.published_version_no == 1
        assert raw.scalar(select(func.count()).select_from(ApprovalRuleVersion)) == 2
        audit.assert_not_awaited()

        candidate.conditions = [condition('gross_margin', 'gt', 22)]
        if operation == 'enable':
            # Enable checks the published snapshot, even when the draft has been corrected.
            with pytest.raises(AppError, match='不能生效'):
                asyncio.run(activate())
            asyncio.run(rules_router.publish_rule(2, None, True, user, session))
        asyncio.run(activate())
        assert candidate.enabled is (operation != 'publish_disabled')
        assert candidate.published_version_no == 2
    engine.dispose()


def test_conflicting_draft_cannot_be_created_or_saved(monkeypatch):
    _register_sqlite_bigint_as_integer()
    engine = create_engine('sqlite://')
    ApprovalRule.__table__.create(engine)
    ApprovalRuleVersion.__table__.create(engine)
    audit = AsyncMock()
    monkeypatch.setattr(rules_router, 'write_audit', audit)
    monkeypatch.setattr(rules_router, 'client_ip', lambda _: None)
    with Session(engine) as raw:
        session = SyncSessionAsAsync(raw)
        session.get_bind = raw.get_bind
        user = SimpleNamespace(id=1)
        active = ApprovalRule(name='高毛利小额自动免审', kind='auto_pass', enabled=True,
                              published_version_no=1, conditions=[condition('gross_margin', 'gte', 25)])
        raw.add(active)
        raw.flush()
        raw.add(ApprovalRuleVersion(rule_id=active.id, version_no=1, payload=rules_router._snapshot(active)))
        raw.commit()
        candidate = rules_router.RulePayload(name='11', kind='express', conditions=[condition('gross_margin', 'gte', 25)])
        with pytest.raises(AppError, match='11.*高毛利小额自动免审.*不能创建') as error:
            asyncio.run(rules_router.create_rule(candidate, None, user, session))
        assert error.value.http_status == 409
        assert raw.scalar(select(func.count()).select_from(ApprovalRule)) == 1
        audit.assert_not_awaited()

        corrected = rules_router.RulePayload(name='11', kind='express', conditions=[condition('gross_margin', 'lt', 25)])
        asyncio.run(rules_router.create_rule(corrected, None, user, session))
        draft = raw.scalar(select(ApprovalRule).where(ApprovalRule.name == '11'))
        assert not draft.enabled and draft.published_version_no == 0
        audit.reset_mock()
        with pytest.raises(AppError, match='11.*高毛利小额自动免审.*不能保存') as error:
            asyncio.run(rules_router.update_rule(draft.id, candidate, None, user, session))
        assert error.value.http_status == 409
        assert draft.conditions == [condition('gross_margin', 'lt', 25)]
        assert raw.scalar(select(func.count()).select_from(ApprovalRuleVersion)) == 1
        audit.assert_not_awaited()
        # Editing an active rule must exclude its own published snapshot.
        asyncio.run(rules_router.update_rule(active.id, candidate, None, user, session))
        assert active.kind == 'express'
        assert asyncio.run(rules_engine.load_active_rules(session))[0][1]['kind'] == 'auto_pass'
    engine.dispose()
