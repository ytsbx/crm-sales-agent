"""B11-06：建部门/建角色的并发重复处理。"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError
from app.modules.user import service as user_service


class _Nested:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


class _EmptyResult:
    def scalar_one_or_none(self):
        return None


class FakeSession:
    """只实现被测函数用到的方法；需要触发异常时由 flush_error 指定。"""

    def __init__(self, *, flush_error: Exception | None = None):
        self.events: list[str] = []
        self.flush_error = flush_error
        self.added: list[object] = []

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.events.append("flush")
        if self.flush_error is not None:
            raise self.flush_error

    def begin_nested(self) -> _Nested:
        return _Nested()

    async def execute(self, *_args, **_kwargs):
        return _EmptyResult()


@pytest.mark.anyio
async def test_create_department_locks_before_name_check(monkeypatch):
    """先拿部门树锁、再查同级重名；顺序反了并发仍会建出两个同名部门。"""
    calls: list[str] = []

    async def lock(session):
        calls.append("lock")

    async def check(session, name, parent_id, exclude_id=None):
        calls.append("check")

    monkeypatch.setattr(user_service, "_lock_department_tree", lock)
    monkeypatch.setattr(user_service, "_assert_sibling_name_free", check)
    session = FakeSession()
    await user_service.create_department(session, {"name": "新部门"})
    assert calls == ["lock", "check"]
    assert session.events == ["flush"]


@pytest.mark.anyio
async def test_create_role_duplicate_race_returns_409():
    """先查后插之间被抢占时，撞唯一约束要翻译成 409 而不是 500。"""
    session = FakeSession(
        flush_error=IntegrityError(
            "INSERT INTO roles", {}, Exception("duplicate key value violates unique constraint")
        )
    )
    with pytest.raises(AppError) as caught:
        await user_service.create_role(session, {"code": "sales", "name": "销售"})
    assert caught.value.http_status == 409
    assert "已存在" in caught.value.message
