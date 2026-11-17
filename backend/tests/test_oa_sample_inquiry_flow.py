"""CRM 打样/询价 → 自研 OA 的首段映射（不连接真实 OA）。"""
import asyncio
import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.base import Base
from app.core.config import settings
from app.modules.customer.model import Customer
from app.modules.dingtalk.model import OaInstance
from app.modules.file.model import BusinessFile, FileRecord
from app.modules.inquiry.model import CustomInquiry
from app.modules.opportunity.model import Opportunity
from app.modules.oa import client as oa_client
from app.modules.oa import inquiry as oa_inquiry
from app.modules.oa import sample as oa_sample
from app.modules.sample.model import SampleRequest
from app.modules.user.model import Department, User
from tests.conftest import SyncSessionAsAsync, _register_sqlite_bigint_as_integer


def _engine():
    _register_sqlite_bigint_as_integer()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        User.__table__, Department.__table__, Customer.__table__,
        SampleRequest.__table__, FileRecord.__table__, BusinessFile.__table__,
        CustomInquiry.__table__, OaInstance.__table__, Opportunity.__table__,
    ])
    return engine


def _actor(user):
    return SimpleNamespace(id=user.id, dingtalk_userid=user.dingtalk_userid)


@pytest.mark.parametrize("template_routing", [False, True])
def test_sample_oa_payload_uses_human_fields_and_two_approvers(monkeypatch, template_routing):
    engine = _engine()
    monkeypatch.setattr(settings, "oa_sample_template_id", "" if template_routing else str(uuid.uuid4()))
    monkeypatch.setattr(settings, "oa_sample_template_code", "sample-migration")
    monkeypatch.setattr(settings, "oa_sample_approver_dingtalk_ids", "" if template_routing else "ding-a,ding-b")
    monkeypatch.setattr(settings, "oa_sample_approver_mode", "all")
    monkeypatch.setattr(oa_client, "configured", lambda: True)
    captured = []

    async def fake_attachments(*_args, **_kwargs):
        return [{"id": str(uuid.uuid4())}]

    async def fake_create(payload):
        captured.append(payload)
        return {"id": str(uuid.uuid4()), "status": "pending", "external_ref": payload["external_ref"]}

    async def fake_get(_approval_id):
        return {"id": "oa-sample-1", "status": "approved", "external_ref": "x"}

    async def fake_withdraw(_approval_id, _note):
        return {"id": "oa-sample-1", "status": "withdrawn", "external_ref": "x"}

    monkeypatch.setattr(oa_sample, "_attachments", fake_attachments)
    monkeypatch.setattr(oa_client, "create_approval", fake_create)
    monkeypatch.setattr(oa_client, "get_approval", fake_get)
    monkeypatch.setattr(oa_client, "withdraw_approval", fake_withdraw)

    with Session(engine) as raw:
        user = User(id=1, name="业务员", username="sales", password_hash="x",
                    dingtalk_userid="ding-user", status="active",
                    created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
        sample = SampleRequest(id=1, owner_id=1, status="pending", version=1,
                               sample_type="销售客户打样", product_category="高频类产品",
                               maker_unit="义乌打样部", target_completion_date=date(2026, 12, 31),
                               sample_purpose="需求")
        raw.add_all([user, sample])
        raw.commit()
        row, message = asyncio.run(oa_sample.start_sample_approval(
            SyncSessionAsAsync(raw), _actor(user), sample,
            {"sample_type": "销售客户打样", "product_category": "高频类产品",
             "maker_unit": "义乌打样部", "target_date": "2026-12-31", "score": "10",
             "sample_note": "需求"},
        ))
        assert row.provider == "self_oa"
        assert row.status == "pending"
        assert message == "已发起 OA 打样审批"
        payload = captured[0]
        if template_routing:
            assert payload["template_code"] == "sample-migration"
            assert "approver_dingtalk_user_ids" not in payload
            assert "approver_mode" not in payload
        else:
            assert payload["approver_dingtalk_user_ids"] == ["ding-a", "ding-b"]
            assert payload["approver_mode"] == "all"
        assert payload["fields"]["sample_type"] == "销售客户打样"
        assert payload["fields"]["sample_note"] == "需求"
        assert payload["fields"]["sample_items"]
        # 审批通过回写样品主表
        asyncio.run(oa_sample.sync_sample_row(SyncSessionAsAsync(raw), row))
        raw.refresh(sample)
        assert row.status == "approved"
        assert sample.status == "approved"
        # 撤回回写审批行
        asyncio.run(oa_sample.withdraw_sample_row(SyncSessionAsAsync(raw), row, "测试撤回"))
        assert row.status == "withdrawn"


@pytest.mark.parametrize("template_routing", [False, True])
def test_inquiry_oa_payload_keeps_quote_fields_for_handler(monkeypatch, tmp_path, template_routing):
    engine = _engine()
    monkeypatch.setattr(settings, "oa_inquiry_template_id", "" if template_routing else str(uuid.uuid4()))
    monkeypatch.setattr(settings, "oa_inquiry_template_code", "inquiry-migration")
    monkeypatch.setattr(settings, "file_root", str(tmp_path))
    (tmp_path / "product.png").write_bytes(b"test-image")
    monkeypatch.setattr(settings, "oa_inquiry_approver_dingtalk_id", "" if template_routing else "ding-li")
    monkeypatch.setattr(oa_client, "configured", lambda: True)
    captured = []

    async def fake_create(payload):
        captured.append(payload)
        return {"id": str(uuid.uuid4()), "status": "pending", "external_ref": payload["external_ref"]}

    async def fake_get(_approval_id):
        return {"id": "oa-inquiry-1", "status": "approved", "external_ref": "x"}

    async def fake_upload(*_args):
        return {"id": "image-in-oa"}

    monkeypatch.setattr(oa_client, "upload_file", fake_upload)
    monkeypatch.setattr(oa_client, "create_approval", fake_create)
    monkeypatch.setattr(oa_client, "get_approval", fake_get)

    with Session(engine) as raw:
        user = User(id=1, name="业务员", username="sales", password_hash="x",
                    dingtalk_userid="ding-user", status="active", department_id=1,
                    created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
        customer = Customer(id=1, name="客户A", status="active", pool_status="private")
        inquiry = CustomInquiry(id=1, title="定制产品", inquiry_no="XQ-1", description="需求",
                                quantity=100, customer_id=1, version=1, status="open")
        raw.add_all([Department(id=1, name="销售部"), user, customer, inquiry,
                     FileRecord(id=1, object_key="product.png", file_name="product.png", mime_type="image/png", size=10),
                     BusinessFile(business_type="inquiry",business_id=1,file_id=1)])
        raw.commit()
        payload = SimpleNamespace(
            extra_fields={"quote_scope": "全系列", "reply_date": "2026-12-15",
                          "provide_sample": "是", "customer_contact": "客户A / 张三",
                          "product_model": "M-1", "delivery_date": "2027-01-30",
                          "special_note": "注意"},
            attachment_file_ids=[],
        )
        row, message = asyncio.run(oa_inquiry.start_inquiry_approval(
            SyncSessionAsAsync(raw), _actor(user), inquiry, payload,
        ))
        assert row.provider == "self_oa"
        assert row.status == "pending"
        assert message == "已发起 OA 定制询价审批"
        sent = captured[0]
        if template_routing:
            assert sent["template_code"] == "inquiry-migration"
            assert "approver_dingtalk_user_id" not in sent
        else:
            assert sent["approver_dingtalk_user_id"] == "ding-li"
        assert sent["fields"]["DDPhotoField_1U7CNXCKQNS00"] == ["image-in-oa"]
        assert sent["attachment_ids"] == ["image-in-oa"]
        assert sent["fields"]["attachments"] == []
        assert sent["fields"]["title"] == "定制产品"
        assert sent["fields"]["quote_scope"] == "全系列"
        assert "quote_files" not in sent["fields"]
        assert "quote_note" not in sent["fields"]
        asyncio.run(oa_inquiry.sync_inquiry_row(SyncSessionAsAsync(raw), row))
        raw.refresh(inquiry)
        assert row.status == "approved"
        assert (inquiry.extra or {})["oa_approval"]["status"] == "approved"


@pytest.mark.parametrize("business_id,category,mime", [
    (2, None, "image/png"), (1, "oa_attachment", "image/png"),
    (1, None, "image/webp"), (1, None, "application/pdf"),
])
def test_product_images_require_own_inquiry_and_supported_image(monkeypatch, business_id, category, mime):
    from app.core.errors import AppError
    engine = _engine()
    uploaded = []
    async def upload(*args):
        uploaded.append(args)
        return {"id":"unexpected"}
    monkeypatch.setattr(oa_client,"upload_file",upload)
    with Session(engine) as raw:
        raw.add_all([FileRecord(id=1,object_key="unused",file_name="file",mime_type=mime,size=1),
                     BusinessFile(business_type="inquiry",business_id=business_id,file_id=1,category=category)])
        raw.commit()
        with pytest.raises(AppError):
            asyncio.run(oa_inquiry._attachments(SyncSessionAsAsync(raw), SimpleNamespace(dingtalk_userid="test"),
                                               SimpleNamespace(id=1), [], images=True))
    assert uploaded == []


@pytest.mark.parametrize('business_status', ['archived', 'converted'])
def test_closed_inquiry_cannot_submit(monkeypatch, business_status):
    from app.core.errors import AppError
    with pytest.raises(AppError, match='只有待评估'):
        oa_inquiry.require_submittable(SimpleNamespace(status=business_status))


@pytest.mark.parametrize('previous,resubmit,rejected', [
    ('pending', False, False), ('submitting', False, False), ('approved', False, False),
    ('needs_review', False, True), ('rejected', False, True), ('withdrawn', False, True),
    ('approved', True, False),
])
def test_inquiry_terminal_and_pending_states_never_create_new_rows(monkeypatch, previous, resubmit, rejected):
    from app.core.errors import AppError
    engine = _engine()
    monkeypatch.setattr(oa_client, 'configured', lambda: True)
    monkeypatch.setattr(settings, 'oa_inquiry_template_id', str(uuid.uuid4()))
    with Session(engine) as raw:
        user = User(id=1,name='测试',username='test',password_hash='x',dingtalk_userid='test-ding',status='active')
        inquiry = CustomInquiry(id=1,title='测试',status='open',version=1)
        row = OaInstance(oa_type='inquiry',inquiry_id=1,inquiry_version=1,provider='self_oa',
                         status=previous,submit_round=1,idempotency_key='existing',created_by=1,created_at=datetime.now(UTC))
        raw.add_all([user,inquiry,row]);raw.commit()
        if rejected:
            with pytest.raises(AppError):
                asyncio.run(oa_inquiry.start_inquiry_approval(SyncSessionAsAsync(raw), _actor(user), inquiry, SimpleNamespace(resubmit=resubmit)))
        else:
            result,_ = asyncio.run(oa_inquiry.start_inquiry_approval(SyncSessionAsAsync(raw), _actor(user), inquiry, SimpleNamespace(resubmit=resubmit)))
            assert result.id == row.id
        assert raw.query(OaInstance).count() == 1


@pytest.mark.parametrize('milestone,expected', [('approved','approved'), ('pending','pending')])
def test_sync_sample_can_release_before_material_steps_end(milestone, expected):
    with Session(_engine()) as raw:
        sample = SampleRequest(id=91, owner_id=1, status='pending', version=1)
        row = OaInstance(oa_type='sample', inquiry_id=91, inquiry_version=1, status='pending',
            idempotency_key='release-sample', submit_round=1, created_at=datetime.now(UTC))
        raw.add_all([sample, row]); raw.commit()
        asyncio.run(oa_sample._apply_sample_summary(SyncSessionAsAsync(raw), row,
            {'status':'pending', 'business_stages': {'sample': {'status':milestone}}}))
        assert row.status == 'pending'
        assert sample.status == expected
        assert sample.source_context['dingtalk_oa']['submitted'] is True
