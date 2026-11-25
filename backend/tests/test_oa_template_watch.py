"""OA 模板变更检测：指纹、变更摘要和通知策略。"""
import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.base import Base
from app.core.config import settings as app_settings
from app.modules.dingtalk.model import OaInstance
from app.modules.notification.model import Notification
from app.modules.oa import template_watch as watch
from app.modules.settings.model import SystemSetting
from app.modules.user.model import Role, User, user_roles
from tests.conftest import SyncSessionAsAsync, _register_sqlite_bigint_as_integer

BASE = {
    "code": "form-21e21dedad1d410c93e996930d012bb3",
    "name": "定制打样审批",
    "version": 1,
    "fields": [
        {"id": "sample_type", "label": "打样类型", "type": "choice",
         "required": True, "options": ["销售客户打样", "设计研发内部打样"]},
        {"id": "sample_details", "label": "样品明细明细表", "type": "attachment",
         "required": True, "sample_files": [{"id": "x"}]},
    ],
    "routing": {"groups": {"initial": {"mode": "any", "approvers": ["u1", "u2"]}}},
}

# CRM 打样模块实际会读取的字段全集，避免集成测试把"缺字段"当成变更。
FULL_BASE = {
    **BASE,
    "fields": BASE["fields"] + [
        {"id": "applicant", "label": "申请人员", "type": "user", "required": True},
        {"id": "product_category", "label": "打样产品大类", "type": "choice",
         "required": True, "options": ["高频类产品"]},
        {"id": "production_unit", "label": "样品制作单位", "type": "choice",
         "required": True, "options": ["义乌打样部"]},
        {"id": "due_date", "label": "要求完成日期", "type": "date", "required": True},
        {"id": "points", "label": "本次打样积分", "type": "number", "required": True},
        {"id": "requirements", "label": "打样需求说明", "type": "text", "required": True},
    ],
}


def test_field_fingerprint_ignores_attachment_samples():
    changed = {**BASE, "fields": [
        {**BASE["fields"][0], "sample_files": [{"id": "another"}]},
        BASE["fields"][1],
    ]}
    assert watch.fingerprint(BASE) == watch.fingerprint(changed)


def test_field_fingerprint_ignores_version_approver_and_people():
    changed = {
        **BASE,
        "version": 2,
        "routing": {"groups": {"initial": {"mode": "all", "approvers": ["u1"]}}},
        "people": [{"id": "u3", "name": "新人"}],
    }
    assert watch.fingerprint(BASE) == watch.fingerprint(changed)


def test_field_fingerprint_changes_on_label_required_options_type():
    label = {**BASE, "fields": [{**BASE["fields"][0], "label": "打样类别"}, BASE["fields"][1]]}
    required = {**BASE, "fields": [{**BASE["fields"][0], "required": False}, BASE["fields"][1]]}
    options = {**BASE, "fields": [{**BASE["fields"][0], "options": ["A"]}, BASE["fields"][1]]}
    type_ = {**BASE, "fields": [{**BASE["fields"][0], "type": "text"}, BASE["fields"][1]]}
    for changed in (label, required, options, type_):
        assert watch.fingerprint(BASE) != watch.fingerprint(changed)


def test_approver_fingerprint_changes_on_approver():
    changed = {**BASE, "routing": {"groups": {"initial": {"mode": "all", "approvers": ["u1"]}}}}
    assert watch.approver_fingerprint(BASE) != watch.approver_fingerprint(changed)


def test_field_change_summary_is_none_for_same_fingerprint():
    snap = watch.snapshot(BASE)
    assert watch.field_change_summary(snap, snap, set()) is None
    assert watch.field_change_summary(None, snap, set()) is None


def test_field_change_summary_reports_version_and_added_field():
    old = watch.snapshot(BASE)
    new = watch.snapshot({**BASE, "version": 2, "fields": BASE["fields"] + [
        {"id": "extra", "label": "新字段", "type": "text", "required": False},
    ]})
    summary = watch.field_change_summary(old, new, set())
    assert summary is not None
    assert "版本 1→2" in summary
    assert "新增未映射字段" in summary
    assert "extra" in summary


def test_field_change_summary_names_removed_expected_field():
    old = watch.snapshot({**BASE, "fields": BASE["fields"] + [
        {"id": "due_date", "label": "要求完成日期", "type": "date", "required": True},
    ]})
    new = watch.snapshot(BASE)
    summary = watch.field_change_summary(old, new, {"due_date"})
    assert summary is not None
    assert "新模板里找不到 CRM 需要的字段" in summary
    assert "due_date（要求完成日期）" in summary


def test_field_change_summary_reports_attribute_changes():
    old = watch.snapshot(BASE)
    new = watch.snapshot({**BASE, "fields": [
        {**BASE["fields"][0], "label": "打样类别", "required": False,
         "type": "text", "options": ["A"]},
        BASE["fields"][1],
    ]})
    summary = watch.field_change_summary(old, new, set())
    assert summary is not None
    assert "标签「打样类型」→「打样类别」" in summary
    assert "取消必填" in summary
    assert "类型 choice→text" in summary
    assert "选项有变化" in summary


def test_expected_field_ids_come_from_module_mappings():
    sample = watch.expected_field_ids("sample")
    assert {"sample_type", "product_category", "production_unit", "due_date",
            "points", "sample_details", "requirements"} <= sample
    inquiry = watch.expected_field_ids("inquiry")
    assert {"customer", "quote_scope", "quoter", "reference_images", "quantity"} <= inquiry
    contract = watch.expected_field_ids("contract")
    assert {"file_category", "our_parties", "contract_name"} <= contract


def test_approver_change_message_only_when_crm_overrides(monkeypatch):
    monkeypatch.setattr(app_settings, "oa_sample_approver_dingtalk_ids", "")
    assert watch.approver_change_message("sample") == ""

    monkeypatch.setattr(app_settings, "oa_sample_approver_dingtalk_ids", "u1")
    message = watch.approver_change_message("sample")
    assert "OA_SAMPLE_APPROVER_DINGTALK_IDS" in message
    assert "清空" in message

    monkeypatch.setattr(app_settings, "oa_inquiry_template_id", "")
    monkeypatch.setattr(app_settings, "oa_inquiry_template_code", "key-account-product-inquiry")
    monkeypatch.setattr(app_settings, "oa_inquiry_approver_dingtalk_id", "u1")
    assert watch.approver_change_message("inquiry") == ""

    monkeypatch.setattr(app_settings, "oa_inquiry_template_code", "another-template")
    assert "OA_INQUIRY_APPROVER_DINGTALK_ID" in watch.approver_change_message("inquiry")


def test_pick_template_by_code_and_latest_version():
    rows = [
        {"id": "i1", "code": "c1", "version": 1},
        {"id": "i1", "code": "c1", "version": 3},
        {"id": "i2", "code": "c2", "version": 2},
    ]
    assert watch.pick_template(rows, "", "c1")["version"] == 3
    assert watch.pick_template(rows, "i2", "c1")["version"] == 2
    assert watch.pick_template(rows, "", "") is None
    assert watch.pick_template(rows, "missing", "") is None


def test_setting_key_stays_within_column_limit():
    assert len(watch.setting_key("sample", BASE["code"])) <= 64
    assert len(watch.setting_key("sample", "x" * 200)) <= 64


def _engine():
    _register_sqlite_bigint_as_integer()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        User.__table__, Role.__table__, user_roles, SystemSetting.__table__,
        Notification.__table__, OaInstance.__table__,
    ])
    return engine


def _seed(raw):
    raw.add(User(id=1, name="管理员", username="admin", password_hash="x",
                 status="active", created_at=datetime.now(UTC), updated_at=datetime.now(UTC)))
    raw.add(Role(id=1, code="admin", name="管理员", status="active"))
    raw.execute(user_roles.insert().values(user_id=1, role_id=1))
    raw.add(OaInstance(
        oa_type="sample", inquiry_id=1, inquiry_version=1, status="approved",
        originator_user_id="user-1", idempotency_key="watch-1", submit_round=1,
        created_at=datetime.now(UTC),
    ))
    raw.commit()


def _bind_watch(monkeypatch, templates):
    monkeypatch.setattr(watch.client, "configured", lambda: True)
    monkeypatch.setattr(watch.client, "list_templates", AsyncMock(return_value=templates))
    for attr, value in (
        ("oa_sample_template_code", BASE["code"]), ("oa_sample_template_id", ""),
        ("oa_contract_template_code", ""), ("oa_contract_template_id", ""),
        ("oa_inquiry_template_code", ""), ("oa_inquiry_template_id", ""),
    ):
        monkeypatch.setattr(app_settings, attr, value)


def test_empty_template_fields_are_skipped():
    engine = _engine()
    with Session(engine) as raw:
        asyncio.run(watch.check_template(
            SyncSessionAsAsync(raw), "sample", {"code": BASE["code"], "fields": []},
        ))
        raw.commit()
        assert raw.query(SystemSetting).count() == 0


def test_first_run_records_and_second_notifies_admin(monkeypatch):
    engine = _engine()
    with Session(engine) as raw:
        _seed(raw)
        templates = [dict(FULL_BASE)]
        _bind_watch(monkeypatch, templates)

        asyncio.run(watch.check_all(SyncSessionAsAsync(raw)))
        raw.commit()
        assert raw.query(SystemSetting).count() == 1
        assert raw.query(Notification).count() == 0

        templates[0] = {
            **FULL_BASE,
            "version": 2,
            "fields": FULL_BASE["fields"] + [
                {"id": "extra", "label": "新字段", "type": "text", "required": False},
            ],
        }
        asyncio.run(watch.check_all(SyncSessionAsAsync(raw)))
        raw.commit()
        notes = raw.query(Notification).all()
        assert len(notes) == 1
        assert notes[0].user_id == 1
        assert notes[0].title == "OA 打样模板字段已变更"
        assert "版本 1→2" in notes[0].content
        assert "新增未映射字段" in notes[0].content
        assert "extra" in notes[0].content


def test_approver_change_notifies_only_when_crm_overrides(monkeypatch):
    engine = _engine()
    with Session(engine) as raw:
        _seed(raw)
        templates = [dict(FULL_BASE)]
        _bind_watch(monkeypatch, templates)
        monkeypatch.setattr(app_settings, "oa_sample_approver_dingtalk_ids", "")

        asyncio.run(watch.check_all(SyncSessionAsAsync(raw)))
        raw.commit()

        templates[0] = {
            **FULL_BASE,
            "routing": {"groups": {"initial": {"mode": "all", "approvers": ["u1"]}}},
        }
        asyncio.run(watch.check_all(SyncSessionAsAsync(raw)))
        raw.commit()
        assert raw.query(Notification).count() == 0

        monkeypatch.setattr(app_settings, "oa_sample_approver_dingtalk_ids", "u9")
        templates[0] = {
            **FULL_BASE,
            "routing": {"groups": {"initial": {"mode": "all", "approvers": ["u2"]}}},
        }
        asyncio.run(watch.check_all(SyncSessionAsAsync(raw)))
        raw.commit()
        notes = raw.query(Notification).all()
        assert len(notes) == 1
        assert notes[0].title == "OA 打样模板审批人配置有冲突"
        assert "OA_SAMPLE_APPROVER_DINGTALK_IDS" in notes[0].content
