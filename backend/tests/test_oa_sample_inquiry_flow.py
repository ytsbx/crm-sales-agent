"""CRM 打样/询价 → 自研 OA 的首段映射（不连接真实 OA）。"""
import asyncio
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import httpx
from fastapi import FastAPI

from app.core.jsonio import DecimalJSONResponse

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.audit import AuditLog
from app.core.base import Base
from app.core.config import settings
from app.core.deps import get_current_user
from app.core.errors import AppError, register_exception_handlers
from app.modules.customer.model import Contact, Customer
from app.modules.customer import service as customer_service
from app.modules.dingtalk.model import OaInstance
from app.modules.file.model import BusinessFile, FileRecord
from app.modules.inquiry.model import CustomInquiry
from app.modules.inquiry import router as inquiry_router
from app.modules.inquiry.schema import CustomInquiryRevise
from app.modules.opportunity.model import Opportunity
from app.modules.oa import client as oa_client
from app.modules.oa import inquiry as oa_inquiry
from app.modules.oa import sample as oa_sample
from app.modules.oa.business_gates import require_sample_approved
from app.modules.sample import dingtalk as sample_approval
from app.modules.sample import router as sample_router
from app.modules.sample.model import SampleItem, SampleRequest, SampleShipment
from app.modules.sample.schema import SampleCreate, SampleTransfer, SampleUpdate
from app.modules.user.model import Department, Permission, Role, User, role_permissions, user_roles
from tests.conftest import SyncSessionAsAsync, _register_sqlite_bigint_as_integer, make_user


def _engine():
    _register_sqlite_bigint_as_integer()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        User.__table__, Department.__table__, Customer.__table__, Contact.__table__,
        SampleRequest.__table__, FileRecord.__table__, BusinessFile.__table__,
        CustomInquiry.__table__, OaInstance.__table__, Opportunity.__table__,
    ])
    return engine


def _actor(user):
    return SimpleNamespace(id=user.id, dingtalk_userid=user.dingtalk_userid)


SAMPLE_FIELDS = {
    "sample_type": "销售客户打样", "product_category": "高频类产品",
    "maker_unit": "义乌打样部", "target_date": "2026-12-31", "score": "10",
    "sample_note": "需求",
}


@pytest.mark.anyio
async def test_new_sample_does_not_fall_back_for_explicit_invalid_owner():
    engine = _engine()
    with Session(engine) as raw:
        raw.add(Customer(id=1, name="测试客户", owner_id=101))
        raw.commit()
        actor = make_user(101, permissions=("sample:manage",))
        request = Request({"type": "http", "headers": []})
        with pytest.raises(AppError, match="负责人 id=0 不存在") as error:
            await sample_router.create_sample(
                SampleCreate(customer_id=1, owner_id=0, use_dingtalk=True),
                request, actor, SyncSessionAsAsync(raw),
            )
        assert error.value.http_status == 404
        assert raw.query(SampleRequest).count() == 0
    engine.dispose()


@pytest.mark.anyio
async def test_revision_without_approval_source_starts_a_tongxu_draft(monkeypatch):
    notify = AsyncMock()
    monkeypatch.setattr(sample_router.followup_service, "record_and_notify", notify)
    monkeypatch.setattr(sample_router.notification_service, "dispatch_pending", AsyncMock())
    engine = _engine()
    Base.metadata.create_all(engine, tables=[
        AuditLog.__table__, SampleItem.__table__, SampleShipment.__table__,
    ])
    actor = make_user(1, permissions=("sample:manage",))
    request = Request({"type": "http", "headers": []})
    with Session(engine) as raw:
        original = SampleRequest(id=1, owner_id=1, status="approved",
                                 made_at=datetime.now(UTC), source_context=None)
        raw.add(original)
        raw.commit()
        revised = (await sample_router.revise_sample(
            original.id, request, user=actor, session=SyncSessionAsAsync(raw)
        ))["data"]

        assert revised["parent_id"] == original.id
        assert revised["version"] == 2 and revised["status"] == "pending"
        assert revised["made_at"] is None
        assert revised["source_context"]["dingtalk_oa"] == {
            "mode": "standalone", "fields": {}, "requires_self_oa": True,
        }
        assert original.status == "approved" and original.made_at is not None
        notify.assert_not_awaited()


@pytest.mark.anyio
async def test_local_review_endpoints_no_longer_accept_approval_or_resubmission():
    app = FastAPI(default_response_class=DecimalJSONResponse)
    register_exception_handlers(app)
    app.include_router(sample_router.router)
    app.dependency_overrides[get_current_user] = lambda: make_user(101, permissions=("sample:manage",))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for action in ("approve", "resubmit"):
            response = await client.post(f"/samples/1/{action}", json={"approved": True})
            assert response.status_code == 409
            assert "同序 OA" in response.json()["message"]


@pytest.mark.anyio
@pytest.mark.parametrize("submitted", [False, True])
async def test_sample_handover_grants_only_the_assigned_sample(monkeypatch, submitted):
    monkeypatch.setattr(sample_router.followup_service, "record_and_notify", AsyncMock())
    monkeypatch.setattr(sample_router.notification_service, "dispatch_after_commit", AsyncMock())
    engine = _engine()
    for table in (AuditLog.__table__, SampleItem.__table__, SampleShipment.__table__, Role.__table__,
                  Permission.__table__, user_roles, role_permissions):
        table.create(engine, checkfirst=True)
    # B22-01 后转交目标须在操作者数据范围内；本用例考的是"交接只授权这一张单"，
    # 操作者范围给 all 以聚焦该断言（范围拦截另有 test_decimal_rework_a1 覆盖）
    manager = make_user(101, permissions=("sample:manage", "sample:view"), data_scope="all")
    recipient = make_user(102, permissions=("sample:manage", "sample:view", "customer:view"))
    request = Request({"type": "http", "headers": []})
    with Session(engine, expire_on_commit=False) as raw:
        raw.add_all([
            User(id=101, name="原负责人", username="original", password_hash="fixture"),
            User(id=102, name="接手人", username="recipient", password_hash="fixture"),
            Customer(id=1, name="原客户", owner_id=101),
            Role(id=1, name="打样管理", code="sample_manager"),
            Permission(id=1, name="看打样", code="sample:view"),
            Permission(id=2, name="办打样", code="sample:manage"),
        ])
        raw.flush()
        raw.execute(user_roles.insert().values(user_id=102, role_id=1))
        raw.execute(role_permissions.insert(), [{"role_id": 1, "permission_id": i} for i in (1, 2)])
        raw.commit()
        session = SyncSessionAsAsync(raw)
        created = await sample_router.create_sample(SampleCreate(customer_id=1), request, manager, session)
        sample = raw.get(SampleRequest, created["data"]["id"])
        meta = sample.source_context["dingtalk_oa"]
        assert meta["mode"] == "standalone"
        if submitted:
            meta = {**meta, "submitted": True, "provider": "self_oa", "instance_id": "oa-1"}
            sample.source_context = {"dingtalk_oa": meta}
        another = SampleRequest(customer_id=1, owner_id=101, created_by=101, status="pending")
        raw.add(another)
        raw.commit()

        with pytest.raises(AppError):
            await sample_router.get_sample(sample.id, recipient, session)
        options = await sample_router.sample_transfer_options(sample.id, "接手", manager, session)
        assert options["data"] == [{"id": 102, "name": "接手人"}]
        await sample_router.transfer_sample(sample.id, SampleTransfer(owner_id=102), request, manager, session)
        assert sample.created_by == 101 and sample.source_context["dingtalk_oa"] == meta
        assert (await sample_router.get_sample(sample.id, recipient, session))["data"]["owner_id"] == 102
        visible = await sample_router.svc.apply_data_scope(select(SampleRequest.id), recipient, session)
        assert list(raw.scalars(visible)) == [sample.id]
        with pytest.raises(AppError) as error:
            await customer_service.get_visible_customer(session, recipient, 1)
        assert error.value.http_status == 403
        if not submitted:
            updated = await sample_router.update_sample(sample.id, SampleUpdate(remark="接手后补充"), request,
                                                        recipient, session)
            assert updated["data"]["remark"] == "接手后补充"
        audit = raw.query(AuditLog).filter_by(action="transfer").one()
        assert audit.before_data == {"owner_id": 101} and audit.after_data == {"owner_id": 102}
    engine.dispose()


@pytest.mark.anyio
async def test_shipped_oa_sample_revision_requires_its_own_approval(monkeypatch):
    monkeypatch.setattr(oa_client, "configured", lambda: True)
    monkeypatch.setattr(settings, "oa_sample_template_code", "test-sample")
    monkeypatch.setattr(oa_sample, "_attachments", AsyncMock(return_value=[{"id": "new-file"}]))
    create = AsyncMock(return_value={"id": "new-approval", "status": "pending"})
    monkeypatch.setattr(oa_client, "create_approval", create)
    monkeypatch.setattr(sample_router.followup_service, "record_and_notify", AsyncMock())
    monkeypatch.setattr(sample_router.notification_service, "dispatch_pending", AsyncMock())
    engine = _engine()
    Base.metadata.create_all(engine, tables=[
        AuditLog.__table__, SampleItem.__table__, SampleShipment.__table__,
    ])
    actor = make_user(1, permissions=("sample:manage",))
    request = Request({"type": "http", "headers": []})
    now = datetime.now(UTC)
    original_context = {"no": "Q001", "dingtalk_oa": {
        "mode": "standalone", "submitted": True, "provider": "self_oa",
        "instance_id": "old-approval", "fields": {
            "sample_note": "旧申请", "sample_items": "old-file",
            "sample_type": "销售客户打样", "_product_size": "原宽度 30 cm",
        },
    }}
    application_fields = {
        "product_category": "高频类产品", "maker_unit": "外协厂",
        "target_completion_date": date(2026, 12, 31),
        "estimated_hours": Decimal("1.50"), "sample_purpose": "定制改款样",
        "urgency": "急", "sample_situation": "客户更改", "sample_fee": Decimal(0),
        "deliver_date": date(2027, 1, 2), "sample_maker": "打样师傅",
    }

    with Session(engine) as raw:
        raw.add_all([
            Department(id=1, name="销售部"),
            User(id=1, name="经办人", username="sales", password_hash="x", dingtalk_userid="person", department_id=1),
            SampleRequest(id=1, owner_id=1, status="shipped", made_at=now, shipped_at=now,
                          source_context=original_context, material_spec="原纸板", **application_fields),
            SampleItem(sample_request_id=1, item_name="外箱", quantity=1),
            SampleShipment(sample_request_id=1, carrier="快递", tracking_no="OLD-001", shipped_at=now),
            OaInstance(oa_type="sample", provider="self_oa", inquiry_id=1, inquiry_version=1,
                       instance_id="old-approval", idempotency_key="old-sample", status="approved",
                       result={"business_stages": {"sample": {"status": "approved"}}},
                       submit_round=1, created_at=now),
        ])
        raw.commit()
        session = SyncSessionAsAsync(raw)

        revised = (await sample_router.revise_sample(1, request, user=actor, session=session))["data"]
        revised_id = revised["id"]
        child = raw.get(SampleRequest, revised_id)
        assert revised["parent_id"] == 1 and revised["version"] == 2
        assert child.source_context["no"] == "Q001" and child.material_spec == "原纸板"
        assert revised["items"][0]["sku_name"] == "外箱"
        assert revised["made_at"] is None and revised["shipments"] == []
        assert {field: getattr(child, field) for field in application_fields} == application_fields
        assert revised["craft_points"] == 15
        prefill = (await sample_approval.detail(revised_id, actor, session))["data"]["draft"]["fields"]
        assert prefill["maker_unit"] == "外协厂打样"
        assert prefill["sample_type"] == "销售客户打样"
        assert prefill["_product_size"] == "原宽度 30 cm"
        assert Decimal(prefill["_estimated_hours"]) == Decimal("1.50")
        assert Decimal(prefill["_sample_fee"]) == 0
        assert prefill["sample_note"] == "旧申请" and "sample_items" not in prefill
        assert raw.get(SampleRequest, 1).source_context == original_context
        with monkeypatch.context() as native:
            native.setattr(settings, "oa_sample_template_code", oa_sample.SOURCE_CODE)
            native.setattr(oa_sample, "native_form", AsyncMock(return_value={
                "provider": "self_oa", "native_form": True, "fields": oa_sample.oa_template_fields(),
            }))
            native_prefill = (await sample_approval.detail(revised_id, actor, session))["data"]["draft"]["fields"]
            assert Decimal(native_prefill["score"]) == 15
            assert native_prefill["sample_note"] == "旧申请"
        assert raw.query(SampleShipment).one().tracking_no == "OLD-001"
        with pytest.raises(AppError, match="原版记录已锁定"):
            await sample_router.revise_sample(1, request, user=actor, session=session)
        with pytest.raises(AppError, match="尚未通过"):
            await require_sample_approved(session, child)
        with pytest.raises(AppError, match="须单独提交"):
            await sample_approval.submit(revised_id, sample_approval.Draft(mode="inquiry", source_oa_id=1),
                                         request, actor, session)
        with monkeypatch.context() as fallback:
            fallback.setattr(oa_client, "configured", lambda: False)
            with pytest.raises(AppError, match="暂不能提交审批"):
                await sample_approval.submit(revised_id, sample_approval.Draft(mode="standalone"),
                                             request, actor, session)
        create.assert_not_awaited()
        draft = sample_approval.Draft(mode="standalone", fields={
            **prefill, "sample_note": "新版加宽两厘米", "_product_size": "宽度 32 cm",
        })
        await sample_approval.save_draft(revised_id, draft, request, actor, session)
        assert sample_approval.metadata(child)["requires_self_oa"] is True
        detail = (await sample_approval.detail(revised_id, actor, session))["data"]
        assert detail["records"] == [] and detail["candidates"] == []
        assert detail["draft"].get("submitted") is None

        await sample_approval.submit(revised_id, draft, request, actor, session)
        await sample_approval.submit(revised_id, draft, request, actor, session)
        create.assert_awaited_once()
        assert sample_approval.metadata(child)["fields"]["_product_size"] == "宽度 32 cm"
        new = raw.query(OaInstance).filter_by(inquiry_id=revised_id).one()
        assert (new.inquiry_version, new.submit_round, new.provider, new.instance_id) == (2, 1, "self_oa", "new-approval")
        assert raw.query(OaInstance).filter_by(inquiry_id=1).one().instance_id == "old-approval"
        with pytest.raises(AppError, match="尚未通过"):
            await require_sample_approved(session, child)
        await oa_sample._apply_sample_summary(session, new, {"id": "new-approval", "status": "approved",
                                                             "business_stages": {"sample": {"status": "approved"}}})
        assert child.status == "approved"
        assert raw.get(SampleRequest, 1).status == "shipped"
        assert (await require_sample_approved(session, child)).id == new.id


@pytest.mark.parametrize("field,value,message", [
    ("department", None, "归属部门"),
    ("score", "", "打样积分"),
    ("score", "NaN", "打样积分"),
    ("score", "99991", "打样积分"),
    ("sample_type", "无效选项", "打样类型"),
    ("target_date", "2026-02-30", "要求完成日期"),
    ("sample_note", " ", "打样需求说明"),
])
def test_sample_oa_validates_before_upload(monkeypatch, field, value, message):
    monkeypatch.setattr(oa_client, "configured", lambda: True)
    monkeypatch.setattr(settings, "oa_sample_template_code", "test-sample")
    upload = AsyncMock(return_value=[{"id": "test-file"}])
    create = AsyncMock(return_value={"id": "test-approval", "status": "pending"})
    monkeypatch.setattr(oa_sample, "_attachments", upload)
    monkeypatch.setattr(oa_client, "create_approval", create)
    with Session(_engine()) as raw:
        user = User(id=1, name="经办人", username="sales", password_hash="x", dingtalk_userid="person",
                    department_id=None if field == "department" else 1)
        sample = SampleRequest(id=1, owner_id=1, status="pending", version=1)
        raw.add_all([Department(id=1, name="销售部"), user, sample])
        raw.commit()
        fields = dict(SAMPLE_FIELDS)
        if field != "department":
            fields[field] = value
        with pytest.raises(AppError, match=message):
            asyncio.run(oa_sample.start_sample_approval(SyncSessionAsAsync(raw), _actor(user), sample, fields))
        assert raw.query(OaInstance).count() == 0
        upload.assert_not_awaited()
        create.assert_not_awaited()


@pytest.mark.parametrize("kind", ["sample", "inquiry"])
def test_unknown_approval_outcome_keeps_round_locked_for_review(monkeypatch, kind):
    monkeypatch.setattr(oa_client, "configured", lambda: True)
    monkeypatch.setattr(settings, f"oa_{kind}_template_code", "test-template")
    create = AsyncMock(side_effect=oa_client.OaUnknownOutcome("未收到发单回执"))
    monkeypatch.setattr(oa_client, "create_approval", create)
    monkeypatch.setattr(oa_sample, "_attachments", AsyncMock(return_value=[{"id": "test-file"}]))
    monkeypatch.setattr(oa_inquiry, "_attachments", AsyncMock(return_value=[{"id": "test-file"}]))
    with Session(_engine()) as raw:
        user = User(id=1, name="经办人", username="sales", password_hash="x", dingtalk_userid="person", department_id=1)
        customer = Customer(id=1, name="客户", owner_id=1)
        sample = SampleRequest(id=1, owner_id=1, customer_id=1, status="pending", version=1)
        inquiry = CustomInquiry(id=1, title="询价", customer_id=1, status="open",
                                inquiry_no="XQ-1", description="打样材质", quantity=5)
        raw.add_all([Department(id=1, name="销售部"), user, customer, sample, inquiry])
        raw.commit()
        session = SyncSessionAsAsync(raw)

        def start():
            if kind == "sample":
                return asyncio.run(oa_sample.start_sample_approval(session, _actor(user), sample, SAMPLE_FIELDS))
            payload = SimpleNamespace(extra_fields={"quote_scope": "全系列", "reply_date": "2026-12-15",
                                                    "provide_sample": "是", "product_model": "M-1"},
                                      attachment_file_ids=[])
            return asyncio.run(oa_inquiry.start_inquiry_approval(session, _actor(user), inquiry, payload))

        with pytest.raises(AppError, match="结果未知"):
            start()
        assert raw.query(OaInstance).one().status == "needs_review"
        with pytest.raises(AppError, match="待核对"):
            start()
        create.assert_awaited_once()


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
        assert not raw.in_transaction()
        captured.append(payload)
        return {"id": str(uuid.uuid4()), "status": "pending", "external_ref": payload["external_ref"]}

    async def fake_get(_approval_id):
        return {"id": "oa-sample-1", "status": "approved", "external_ref": "x",
                "business_stages": {"sample": {"status": "approved"}}}

    async def fake_withdraw(_approval_id, _note):
        return {"id": "oa-sample-1", "status": "withdrawn", "external_ref": "x"}

    monkeypatch.setattr(oa_sample, "_attachments", fake_attachments)
    monkeypatch.setattr(oa_client, "create_approval", fake_create)
    monkeypatch.setattr(oa_client, "get_approval", fake_get)
    monkeypatch.setattr(oa_client, "withdraw_approval", fake_withdraw)

    with Session(engine) as raw:
        user = User(id=1, name="业务员", username="sales", password_hash="x", department_id=1,
                    dingtalk_userid="ding-user", status="active",
                    created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
        sample = SampleRequest(id=1, owner_id=1, status="pending", version=1,
                               sample_type="销售客户打样", product_category="高频类产品",
                               maker_unit="义乌打样部", target_completion_date=date(2026, 12, 31),
                               sample_purpose="需求")
        raw.add_all([Department(id=1, name="销售部"), user, sample])
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
        assert payload["title"] == f"打样申请 · {sample.id}"
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


@pytest.mark.anyio
async def test_native_sample_department_comes_from_oa_identity(monkeypatch):
    """同序模板的申请人部门是完整的；CRM 这边没挂部门也不能挡住或发空值。"""
    engine = _engine()
    monkeypatch.setattr(settings, "oa_sample_template_code", oa_sample.SOURCE_CODE)
    monkeypatch.setattr(oa_client, "configured", lambda: True)
    captured = []

    async def fake_templates(_user):
        return [{
            "id": "tpl", "code": oa_sample.SOURCE_CODE, "version": 1,
            "applicant": {"id": "person-1", "name": "业务员",
                          "department_id": "dept-1", "department_name": "同序销售部"},
            "fields": [
                {"id": "sample_type", "type": "choice", "label": "打样类型", "required": True,
                 "options": ["销售客户打样", "设计研发内部打样"]},
                {"id": "product_category", "type": "choice", "label": "打样产品大类", "required": True,
                 "options": ["高频类产品", "车缝类产品"]},
                {"id": "maker_unit", "type": "choice", "label": "样品制作单位", "required": True,
                 "options": ["义乌打样部", "山东生产部", "外协厂打样"]},
                {"id": "target_date", "type": "date", "label": "要求完成日期", "required": True,
                 "options": []},
                {"id": "score", "type": "number", "label": "本次打样积分", "required": True,
                 "options": []},
                {"id": "sample_note", "type": "text", "label": "打样需求说明", "required": True,
                 "options": []},
                {"id": "sample_items", "type": "attachment", "label": "样品明细明细表", "required": True,
                 "options": []},
            ],
        }]

    async def fake_create(payload):
        captured.append(payload)
        return {"id": "oa-sample-1", "status": "pending", "external_ref": payload["external_ref"]}

    monkeypatch.setattr(oa_client, "list_templates", fake_templates)
    monkeypatch.setattr(oa_client, "create_approval", fake_create)
    monkeypatch.setattr(oa_sample, "_selected_detail_files", AsyncMock(return_value=[{"id": "test-file"}]))
    with Session(engine) as raw:
        user = User(id=1, name="业务员", username="sales", password_hash="x",
                    dingtalk_userid="ding-user", status="active", department_id=None,
                    created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
        sample = SampleRequest(id=1, owner_id=1, status="pending", version=1)
        raw.add_all([user, sample])
        raw.commit()
        await oa_sample.start_sample_approval(
            SyncSessionAsAsync(raw), _actor(user), sample, dict(SAMPLE_FIELDS),
            sample_detail_file_ids=[1],
        )
    assert captured[0]["fields"]["department"] == "同序销售部"


@pytest.mark.anyio
async def test_inquiry_revision_keeps_saved_quote_owner():
    engine = _engine()
    AuditLog.__table__.create(engine, checkfirst=True)
    with Session(engine) as raw:
        old = CustomInquiry(id=1, title="定制产品", inquiry_no="XQ-1", created_by=101,
                            oa_quote_user_id="previous-quoter", oa_quote_user_name="原报价员",
                            extra={"oa_draft": {"quote_owner_user_id": "draft-quoter"}})
        raw.add(old)
        raw.commit()
        result = await inquiry_router.revise_inquiry(
            1, CustomInquiryRevise(title="新版定制产品"), Request({"type": "http", "headers": []}),
            make_user(101), SyncSessionAsAsync(raw),
        )
        revised = raw.get(CustomInquiry, result["data"]["id"])
        assert revised.version == 2 and revised.oa_quote_user_name == "原报价员"
        assert revised.oa_quote_user_id == old.oa_quote_user_id
        assert oa_inquiry.saved_quote_owner(revised) == "draft-quoter"
    engine.dispose()


@pytest.mark.parametrize("template_routing", [False, True, "native"])
@pytest.mark.parametrize("customer_id", [1, None])
def test_inquiry_oa_payload_keeps_quote_fields_for_handler(monkeypatch, tmp_path, template_routing, customer_id):
    native = template_routing == "native"
    quoter = str(uuid.uuid4())
    identity = {"id": str(uuid.uuid4()), "department_id": str(uuid.uuid4())}
    engine = _engine()
    async def fake_templates(_user):
        return [{"code": oa_inquiry.NATIVE_INQUIRY_CODE, "version": 1,
                 "fields": [{"id": "quoter", "user_scope": [quoter]}],
                 "people": [{"id": quoter}], "applicant": identity}]
    monkeypatch.setattr(oa_client, "list_templates", fake_templates)
    monkeypatch.setattr(settings, "oa_inquiry_template_id", "" if template_routing else str(uuid.uuid4()))
    monkeypatch.setattr(settings, "oa_inquiry_template_code", oa_inquiry.NATIVE_INQUIRY_CODE if native else "inquiry-migration")
    monkeypatch.setattr(settings, "file_root", str(tmp_path))
    (tmp_path / "product.png").write_bytes(b"test-image")
    monkeypatch.setattr(settings, "oa_inquiry_approver_dingtalk_id", "" if template_routing else "ding-li")
    monkeypatch.setattr(oa_client, "configured", lambda: True)
    captured = []

    async def fake_create(payload):
        assert not raw.in_transaction()
        captured.append(payload)
        return {"id": str(uuid.uuid4()), "status": "pending", "external_ref": payload["external_ref"]}

    async def fake_get(_approval_id):
        return {"id": "oa-inquiry-1", "status": "approved", "external_ref": "x"}

    async def fake_upload(*_args):
        assert not raw.in_transaction()
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
                                quantity=100, customer_id=customer_id, version=1, status="open",
                                extra={"oa_draft": {"quote_owner_user_id": quoter}} if native else None)
        raw.add_all([Department(id=1, name="销售部"), user, customer, inquiry,
                     FileRecord(id=1, object_key="product.png", file_name="product.png", mime_type="image/png", size=10),
                     BusinessFile(business_type="inquiry",business_id=1,file_id=1)])
        raw.commit()
        payload = SimpleNamespace(
            extra_fields={"quote_scope": "全系列", "reply_date": "2026-12-15",
                          "provide_sample": "是", "customer_contact": "客户A / 张三",
                          "product_model": "M-1", "delivery_date": "2027-01-30",
                          "special_note": "注意"},
            quote_owner_user_id=None if native else quoter, attachment_file_ids=[],
        )
        row, message = asyncio.run(oa_inquiry.start_inquiry_approval(
            SyncSessionAsAsync(raw), _actor(user), inquiry, payload,
        ))
        assert row.provider == "self_oa"
        assert row.status == "pending"
        assert row.customer_id == customer_id
        assert message == "已发起 OA 定制询价审批"
        sent = captured[0]
        assert sent["fields"]["customer" if native else "customer_contact"] == "客户A / 张三"
        if native:
            assert sent["template_code"] == oa_inquiry.NATIVE_INQUIRY_CODE
            assert "approver_dingtalk_user_id" not in sent
            assert sent["fields"]["quoter"] == quoter
            assert sent["fields"]["reporter"] == identity["id"]
            assert sent["fields"]["report_department"] == identity["department_id"]
        elif template_routing:
            assert sent["template_code"] == "inquiry-migration"
            assert "approver_dingtalk_user_id" not in sent
        else:
            assert sent["approver_dingtalk_user_id"] == "ding-li"
        assert sent["fields"]["reference_images" if native else "DDPhotoField_1U7CNXCKQNS00"] == ["image-in-oa"]
        assert sent["attachment_ids"] == ["image-in-oa"]
        assert sent["fields"]["attachments"] == []
        assert sent["fields"]["product_name" if native else "title"] == "定制产品"
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


def test_sync_sample_without_stage_summary_follows_overall_approved():
    """现行模板不产出阶段摘要（2026-11-25 实测）：整单通过即回写 approved。"""
    with Session(_engine()) as raw:
        sample = SampleRequest(id=92, owner_id=1, status='pending', version=1)
        row = OaInstance(oa_type='sample', inquiry_id=92, inquiry_version=1, status='pending',
            idempotency_key='release-sample-compat', submit_round=1, created_at=datetime.now(UTC))
        raw.add_all([sample, row]); raw.commit()
        asyncio.run(oa_sample._apply_sample_summary(SyncSessionAsAsync(raw), row, {'status': 'approved'}))
        assert row.status == 'approved'
        assert sample.status == 'approved'


def test_sample_detail_upload_requires_files_from_the_same_sample():
    from app.modules.oa.sample import _selected_detail_files

    engine = _engine()
    with Session(engine) as raw:
        raw.add(FileRecord(id=1, object_key="detail.xlsx", file_name="detail.xlsx", size=1))
        raw.add(BusinessFile(business_type="sample", business_id=2, file_id=1, category="sample_details"))
        raw.add(SampleRequest(id=1, owner_id=1, status="pending", version=1))
        raw.commit()
        sample = raw.get(SampleRequest, 1)
        user = SimpleNamespace(id=1, dingtalk_userid="bound")
        session = SyncSessionAsAsync(raw)
        with pytest.raises(AppError, match="请上传样品明细明细表"):
            asyncio.run(_selected_detail_files(session, user, sample, []))
        with pytest.raises(AppError, match="不属于当前打样单"):
            asyncio.run(_selected_detail_files(session, user, sample, [1]))
