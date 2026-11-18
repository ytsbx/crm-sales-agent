"""规则发布/启用被 409 拦下的接线验证（内存 SQLite，不碰真实库）。

只调 `_conflicts_with_enabled_rules`：它是 publish / toggle 共用的那一段。
用例要证明三件事：
1. 撞在同一个边界上会抛 409，message 里带**两边规则名**和阈值；
2. 「> 22」与「≤ 22」互斥，不会抛；
3. 停用的规则、草稿（无发布版本）不参与判定 —— 它们根本不会被求值。
"""

import pytest

from app.core.errors import AppError
from app.modules.approval.model import ApprovalRule, ApprovalRuleVersion
from app.modules.approval.rules_router import _conflicts_with_enabled_rules
from tests.conftest import SyncSessionAsAsync


def _prepare(session):
    engine = session.get_bind()
    ApprovalRule.__table__.create(engine, checkfirst=True)
    ApprovalRuleVersion.__table__.create(engine, checkfirst=True)


def _add_rule(session, *, name, op, value, enabled=True, published=True):
    rule = ApprovalRule(
        name=name,
        kind="auto_pass",
        priority=100,
        enabled=enabled,
        conditions=[{"field": "gross_margin", "op": op, "value": value}],
        action={},
        published_version_no=1 if published else 0,
    )
    session.add(rule)
    session.flush()
    if published:
        session.add(
            ApprovalRuleVersion(
                rule_id=rule.id,
                version_no=1,
                payload={
                    "name": name,
                    "kind": rule.kind,
                    "priority": rule.priority,
                    "conditions": rule.conditions,
                    "action": {},
                    "description": None,
                },
            )
        )
        session.flush()
    return rule


@pytest.mark.anyio
async def test_closed_boundary_raises_409_with_names(db_session):
    _prepare(db_session)
    _add_rule(db_session, name="低毛利强制会签", op="lte", value=22)
    candidate = _add_rule(db_session, name="高毛利免审", op="gte", value=22, enabled=False, published=False)
    db_session.commit()

    with pytest.raises(AppError) as error:
        await _conflicts_with_enabled_rules(
            SyncSessionAsAsync(db_session),
            rule=candidate,
            conditions=candidate.conditions,
            verb="发布",
        )
    assert error.value.http_status == 409
    assert "低毛利强制会签" in error.value.message
    assert "高毛利免审" in error.value.message
    assert "≥ 22%" in error.value.message and "≤ 22%" in error.value.message


@pytest.mark.anyio
async def test_open_boundary_does_not_conflict(db_session):
    _prepare(db_session)
    _add_rule(db_session, name="低毛利强制会签", op="lte", value=22)
    candidate = _add_rule(db_session, name="毛利率高于22免审", op="gt", value=22, enabled=False, published=False)
    db_session.commit()

    # 不抛即通过：>22 与 ≤22 在 22% 这个边界上互斥
    await _conflicts_with_enabled_rules(
        SyncSessionAsAsync(db_session),
        rule=candidate,
        conditions=candidate.conditions,
        verb="发布",
    )


@pytest.mark.anyio
async def test_disabled_rule_is_ignored(db_session):
    _prepare(db_session)
    _add_rule(db_session, name="已停用的旧规则", op="lte", value=22, enabled=False)
    candidate = _add_rule(db_session, name="高毛利免审", op="gte", value=22, enabled=False, published=False)
    db_session.commit()

    await _conflicts_with_enabled_rules(
        SyncSessionAsAsync(db_session),
        rule=candidate,
        conditions=candidate.conditions,
        verb="发布",
    )
