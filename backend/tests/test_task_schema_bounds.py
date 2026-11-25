"""B22-08：任务入参边界回归（优先级枚举 / 标题去空白 / 批量上限）。

纯 schema 级断言，不依赖数据库：非法值必须在进库前被 422 拒掉。
"""
import pytest
from pydantic import ValidationError

from app.modules.task.schema import TaskBatchComplete, TaskCreate, TaskUpdate


def test_task_create_priority_must_be_enum():
    assert TaskCreate(title="正常", priority="high").priority == "high"
    with pytest.raises(ValidationError):
        TaskCreate(title="正常", priority="urgent")


def test_task_create_title_rejects_blank():
    with pytest.raises(ValidationError):
        TaskCreate(title="   ")
    assert TaskCreate(title="  带空白  ").title == "带空白"


def test_task_update_priority_and_title_bounds():
    with pytest.raises(ValidationError):
        TaskUpdate(priority="whatever")
    with pytest.raises(ValidationError):
        TaskUpdate(title="  ")
    # 不传 title 表示不改，允许
    assert TaskUpdate(priority="low").title is None


def test_task_batch_complete_ids_bounded():
    with pytest.raises(ValidationError):
        TaskBatchComplete(task_ids=[])
    with pytest.raises(ValidationError):
        TaskBatchComplete(task_ids=list(range(201)))
    assert TaskBatchComplete(task_ids=[1, 2]).task_ids == [1, 2]