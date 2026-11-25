from datetime import UTC, datetime
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.errors import AppError
from app.modules.contract import router as routes


@pytest.mark.anyio
async def test_restore_preserves_contract_state_and_checks_scope(monkeypatch):
    doc = Obj(id=7, doc_no='CT-7', customer_id=3, deleted_at=datetime.now(UTC), status='void', filled_data={'original': 12})
    session = Obj(execute=AsyncMock(return_value=Obj(scalar_one_or_none=lambda: doc)),
                  get=AsyncMock(return_value=Obj(deleted_at=None)), commit=AsyncMock())
    scope = AsyncMock()
    monkeypatch.setattr(routes, '_ensure_doc_in_scope', scope)
    monkeypatch.setattr(routes, 'write_audit', AsyncMock())
    monkeypatch.setattr(routes, 'client_ip', Mock(return_value='127.0.0.1'))
    await routes.restore_document(7, Obj(), Obj(id=1), session)
    scope.assert_awaited_once()
    assert doc.deleted_at is None
    assert doc.status == 'void' and doc.filled_data == {'original': 12}
    session.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_restore_rejects_deleted_customer(monkeypatch):
    deleted = datetime.now(UTC)
    doc = Obj(id=7, customer_id=3, deleted_at=deleted)
    session = Obj(execute=AsyncMock(return_value=Obj(scalar_one_or_none=lambda: doc)),
                  get=AsyncMock(return_value=Obj(deleted_at=deleted)), commit=AsyncMock())
    monkeypatch.setattr(routes, '_ensure_doc_in_scope', AsyncMock())
    with pytest.raises(AppError, match='先恢复'):
        await routes.restore_document(7, Obj(), Obj(id=1), session)
    assert doc.deleted_at == deleted
    session.commit.assert_not_awaited()
