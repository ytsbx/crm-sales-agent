"""CRM 合同 → 自研 OA：发起后本地行、幂等键与签署闸门。"""
import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.base import Base
from app.core.config import settings
from app.modules.contract.model import ContractDocument
from app.modules.customer.model import Customer
from app.modules.dingtalk.model import OaInstance
from app.modules.file.model import FileRecord
from app.modules.oa import client as oa_client
from app.modules.oa import service as oa_service
from app.modules.user.model import User
from tests.conftest import SyncSessionAsAsync, _register_sqlite_bigint_as_integer


def _engine():
    from sqlalchemy import create_engine

    from app.modules.contract.model import ContractTemplate
    from app.modules.order.model import SalesOrder
    from app.modules.quote.model import Quote, QuoteVersion
    from app.modules.settings.model import SystemSetting

    _register_sqlite_bigint_as_integer()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__, Customer.__table__, FileRecord.__table__,
            ContractTemplate.__table__, ContractDocument.__table__,
            SalesOrder.__table__, Quote.__table__, QuoteVersion.__table__,
            OaInstance.__table__, SystemSetting.__table__,
        ],
    )
    return engine


def test_start_contract_creates_self_oa_row_and_sign_gate(tmp_path, monkeypatch):
    from sqlalchemy.orm import Session

    engine = _engine()
    (tmp_path / "contract.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setattr(settings, "file_root", str(tmp_path))
    monkeypatch.setattr(settings, "oa_base_url", "http://oa.test")
    monkeypatch.setattr(settings, "oa_integration_secret", "secret")
    monkeypatch.setattr(settings, "oa_contract_template_id", str(uuid.uuid4()))
    monkeypatch.setattr(settings, "oa_contract_template_code", "")
    monkeypatch.setattr(settings, "oa_contract_approver_dingtalk_id", "ding-fixed-approver")
    monkeypatch.setattr(oa_client, "configured", lambda: True)

    async def fake_upload(*_args, **_kwargs):
        return {"id": str(uuid.uuid4()), "name": "contract.pdf", "size": 14}

    captured = []

    async def fake_create(payload):
        captured.append(payload)
        assert payload["applicant_dingtalk_user_id"] == "ding-user-1"
        assert payload["attachment_ids"]
        assert payload["approver_dingtalk_user_id"] == "ding-fixed-approver"
        return {
            "id": str(uuid.uuid4()), "status": "pending", "stage": "initial",
            "assignee_name": "田小秘", "external_ref": payload["external_ref"],
        }

    monkeypatch.setattr(oa_client, "upload_file", fake_upload)
    monkeypatch.setattr(oa_client, "create_approval", fake_create)

    with Session(engine) as raw:
        user = User(
            id=1, name="业务员", username="sales", password_hash="x",
            dingtalk_userid="ding-user-1", status="active",
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        )
        customer = Customer(id=1, name="客户A", status="active", pool_status="private")
        file_row = FileRecord(id=1, object_key="contract.pdf", file_name="contract.pdf", size=14)
        doc = ContractDocument(
            id=1, doc_no="CT-001", title="测试合同", customer_id=1,
            content_snapshot="正文", status="draft", generated_file_id=1,
            created_at=datetime.now(UTC),
        )
        raw.add_all([user, customer, file_row, doc])
        raw.commit()
        session = SyncSessionAsAsync(raw)

        class Actor:
            id = 1
            dingtalk_userid = "ding-user-1"

        row, message = asyncio.run(oa_service.start_contract_approval(
            session, Actor(), doc,
            our_company="手工填写的我方主体", counterparty_name="手工填写的对方主体",
        ))
        assert row.provider == "self_oa"
        assert row.status == "pending"
        assert row.instance_id
        assert row.idempotency_key.startswith("crm-contract:1:")
        assert message == "已发起 OA 合同审批"
        assert raw.query(OaInstance).count() == 1
        raw.refresh(doc)
        assert doc.filled_data["_dingtalk_oa"]["provider"] == "self_oa"
        assert doc.filled_data["_dingtalk_oa"]["oa_instance_id"] == row.id
        # 主体由经办人填写，系统不能拿 company_name/客户名替换
        assert captured[0]["fields"]["contract_mine_info"] == [
            {"mine_company_name": "手工填写的我方主体"}
        ]
        assert captured[0]["fields"]["contract_opposite_info"] == [
            {"opposite_company_name": "手工填写的对方主体"}
        ]

        assert asyncio.run(oa_service.contract_approval_approved(session, 1)) is False
        row.status = "approved"
        raw.commit()
        assert asyncio.run(oa_service.contract_approval_approved(session, 1)) is True


def test_sign_is_blocked_until_oa_approved(monkeypatch):
    """审批未通过时，回签登记必须被挡；这条是“审批后流程”的闸门。"""
    from types import SimpleNamespace

    from app.core.errors import AppError
    from app.modules.contract import service as contract_service

    doc = ContractDocument(
        id=9, doc_no="CT-009", title="待审批合同", customer_id=1,
        content_snapshot="正文", status="draft",
        filled_data={"_dingtalk_oa": {"provider": "self_oa"}},
        created_at=datetime.now(UTC),
    )

    async def fake_get(*_args, **_kwargs):
        return doc

    async def fake_source(*_args, **_kwargs):
        return None

    async def fake_latest(*_args, **_kwargs):
        return SimpleNamespace(status="pending")

    monkeypatch.setattr(contract_service, "get_doc_or_404", fake_get)
    monkeypatch.setattr(contract_service, "_ensure_signable_source", fake_source)
    monkeypatch.setattr(
        contract_service, "oa_service", SimpleNamespace(latest_contract_row=fake_latest),
        raising=False,
    )
    # sign_document 内部是局部 import，直接替换 oa.service 的 latest 函数。
    from app.modules.oa import service as oa_service_module
    monkeypatch.setattr(oa_service_module, "latest_contract_row", fake_latest)

    with pytest.raises(AppError) as caught:
        asyncio.run(contract_service.sign_document(SimpleNamespace(), doc, file_id=1, note=None))
    assert caught.value.http_status == 422
    assert "OA 合同审批通过后" in caught.value.message
