"""合同模板库：文档管理里的「合同模板」只存档原文件，不恢复系统模板生成。"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.core.base import Base
from app.core.errors import AppError
from app.modules.contract import service
from app.modules.contract.model import ContractDocument, ContractTemplate
from app.modules.contract.schema import TemplateFileCreate
from app.modules.file.model import BusinessFile, FileRecord
from tests.conftest import SyncSessionAsAsync


def _prepare(db_session):
    Base.metadata.create_all(
        db_session.get_bind(),
        tables=[
            model.__table__
            for model in (ContractTemplate, ContractDocument, FileRecord, BusinessFile)
        ],
    )
    db_session.add(
        FileRecord(
            id=11,
            storage_provider="local",
            object_key="tpl-11",
            file_name="货款月结协议_一件代发专用_20261125.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            size=45633,
            uploaded_by=101,
            created_at=datetime.now(UTC),
        )
    )
    db_session.flush()
    return SyncSessionAsAsync(db_session)


@pytest.mark.anyio
async def test_file_template_is_stored_disabled_and_linked(db_session):
    session = _prepare(db_session)
    payload = TemplateFileCreate(
        doc_type="monthly", name="货款月结协议（一件代发专用）", file_id=11
    )
    template, file_row, created = await service.create_file_template(
        session, payload=payload, user_id=101
    )

    assert created is True
    assert template.doc_type == "monthly"
    assert template.version == 1
    # 文件模板不进生成链路：正文为空、未启用
    assert template.body == ""
    assert template.enabled is False
    assert file_row.file_name.endswith(".docx")

    link = db_session.execute(
        select(BusinessFile).where(BusinessFile.business_id == template.id)
    ).scalar_one()
    assert link.business_type == "contract_template"
    assert link.file_id == 11
    assert link.category == "template"

    rows = await service.list_templates(session)
    row = next(item for item in rows if item["id"] == template.id)
    assert row["file"]["file_id"] == 11
    assert row["file"]["file_name"].endswith(".docx")


@pytest.mark.anyio
async def test_same_file_is_not_stored_as_a_duplicate_version(db_session):
    session = _prepare(db_session)
    payload = TemplateFileCreate(doc_type="monthly", name="月结协议模板", file_id=11)

    first, _, created_first = await service.create_file_template(
        session, payload=payload, user_id=101
    )
    second, _, created_second = await service.create_file_template(
        session, payload=payload, user_id=101
    )

    assert created_first is True and created_second is False
    assert second.id == first.id
    count = db_session.execute(select(func.count()).select_from(ContractTemplate)).scalar_one()
    assert count == 1


@pytest.mark.anyio
async def test_missing_file_is_rejected(db_session):
    session = _prepare(db_session)
    payload = TemplateFileCreate(doc_type="contract", name="销售合同模板", file_id=999)
    with pytest.raises(AppError, match="资料文件不存在"):
        await service.create_file_template(session, payload=payload, user_id=101)


@pytest.mark.anyio
async def test_only_own_unattached_file_can_become_a_template(db_session):
    """资料库对全体有订单查看权的人可见，所以不能拿别人名下的文件当资料。"""
    session = _prepare(db_session)
    payload = TemplateFileCreate(doc_type="monthly", name="月结协议模板", file_id=11)
    with pytest.raises(AppError, match="只能把自己刚上传的文件"):
        await service.create_file_template(session, payload=payload, user_id=202)

    # 已经挂在业务数据上的文件同样不许转身变成全员可见的模板
    db_session.add(BusinessFile(business_type="customer", business_id=5, file_id=11))
    db_session.flush()
    with pytest.raises(AppError, match="已经挂在业务数据上"):
        await service.create_file_template(session, payload=payload, user_id=101)
