"""文档 §3.2 的普通跟进与免填约束，纯参数验证。"""
import pytest
from pydantic import ValidationError

from app.modules.followup.schema import FollowUpCreate, FollowUpUpdate


@pytest.mark.parametrize('fields', [
    {}, {'next_action': '回访'}, {'task_due_at': '2026-10-08T09:00:00+08:00'},
    {'next_action': '  ', 'task_due_at': '2026-10-08T09:00:00+08:00'},
    {'next_action': '回访', 'task_due_at': '2026-10-08T09:00:00'},
    {'exemption_reason': 'unknown'}, {'exemption_reason': 'business_closed', 'next_action': '回访'},
    {'exemption_reason': 'waiting_external', 'task_due_at': '2026-10-08T09:00:00+08:00'},
    {'exemption_reason': 'customer_declined', 'create_task': True},
])
def test_incomplete_or_contradictory_plan_rejected(fields):
    with pytest.raises(ValidationError):
        FollowUpCreate(content='已沟通', **fields)


@pytest.mark.parametrize('reason', ['customer_declined', 'business_closed', 'waiting_external'])
def test_explicit_exemption_does_not_require_a_plan(reason):
    payload = FollowUpCreate(content='  已沟通  ', exemption_reason=reason)
    assert payload.content == '已沟通'
    assert payload.next_action is None and payload.task_due_at is None


def test_plan_normalizes_timezone_and_text():
    payload = FollowUpCreate(content='已沟通', next_action=' 回访 ', task_due_at='2026-10-08T09:00:00+08:00')
    assert payload.next_action == '回访'
    assert payload.task_due_at.isoformat() == '2026-10-08T01:00:00+00:00'


@pytest.mark.parametrize('content', [None, '', '  '])
def test_update_cannot_erase_communication(content):
    with pytest.raises(ValidationError):
        FollowUpUpdate(content=content)
