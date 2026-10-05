"""配置页仅提交改动字段，PATCH 不应复用创建时的全部必填参数。"""
import pytest
from pydantic import ValidationError

from app.modules.settings.schema import PublicPoolRuleInput, PublicPoolRuleUpdate, TaskRuleUpdate


def test_task_rule_partial_update_preserves_omitted_fields():
    assert TaskRuleUpdate(trigger_config={'days': 3}).model_dump(exclude_unset=True) == {'trigger_config': {'days': 3}}


def test_pool_rule_partial_update_preserves_omitted_fields():
    assert PublicPoolRuleUpdate(enabled=False).model_dump(exclude_unset=True) == {'enabled': False}


@pytest.mark.parametrize('schema,field', [
    (TaskRuleUpdate, 'name'), (TaskRuleUpdate, 'code'), (TaskRuleUpdate, 'status'),
    (PublicPoolRuleUpdate, 'days'), (PublicPoolRuleUpdate, 'enabled'), (PublicPoolRuleUpdate, 'level'),
])
def test_required_db_fields_cannot_be_explicitly_cleared(schema, field):
    with pytest.raises(ValidationError):
        schema(**{field: None})


@pytest.mark.parametrize('days', [0, -1])
def test_pool_rule_days_must_be_positive(days):
    with pytest.raises(ValidationError):
        PublicPoolRuleInput(level='A', days=days)
