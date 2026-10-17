from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import AppError
from app.modules.contract import router
from app.modules.contract.schema import DocumentCreate


@pytest.mark.anyio
async def test_internal_legacy_fixture_does_not_reenable_public_template_generation(monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr(router, 'create_document_record', create)
    with pytest.raises(AppError, match='系统模板生成已停用'):
        await router.generate_document(DocumentCreate(customer_id=1, template_id=1),
                                       SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    create.assert_not_awaited()


@pytest.mark.anyio
async def test_uploaded_contract_still_requires_origin_and_title(monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr(router, 'create_document_record', create)
    payload = DocumentCreate(customer_id=1, source_type='customer_drafted', original_file_id=2)
    with pytest.raises(AppError, match='合同来源'):
        await router.generate_document(payload, None, None, None)
    payload.contract_origin = 'company'
    with pytest.raises(AppError, match='合同名称'):
        await router.generate_document(payload, None, None, None)
    create.assert_not_awaited()
