"""OA 模板变更检测：只看指纹/摘要的纯函数，不连数据库。"""
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


def test_fingerprint_ignores_attachment_samples():
    changed = {**BASE, "fields": [
        {**BASE["fields"][0], "sample_files": [{"id": "another"}]},
        BASE["fields"][1],
    ]}
    assert watch.fingerprint(BASE) == watch.fingerprint(changed)


def test_fingerprint_ignores_version_only_republish():
    assert watch.fingerprint(BASE) == watch.fingerprint({**BASE, "version": 2})


def test_fingerprint_changes_on_label_required_and_options():
    label = {**BASE, "fields": [{**BASE["fields"][0], "label": "打样类别"}, BASE["fields"][1]]}
    required = {**BASE, "fields": [{**BASE["fields"][0], "required": False}, BASE["fields"][1]]}
    options = {**BASE, "fields": [{**BASE["fields"][0], "options": ["A"]}, BASE["fields"][1]]}
    assert watch.fingerprint(BASE) != watch.fingerprint(label)
    assert watch.fingerprint(BASE) != watch.fingerprint(required)
    assert watch.fingerprint(BASE) != watch.fingerprint(options)


def test_fingerprint_ignores_approver_and_people_changes():
    changed = {
        **BASE,
        "routing": {"groups": {"initial": {"mode": "all", "approvers": ["u1"]}}},
        "people": [{"id": "u3", "name": "新人"}],
    }
    assert watch.fingerprint(BASE) == watch.fingerprint(changed)


def test_change_summary_is_none_for_same_fingerprint():
    snap = watch.snapshot(BASE)
    assert watch.change_summary(snap, snap) is None
    assert watch.change_summary(None, snap) is None


def test_change_summary_reports_version_and_field_count():
    old = watch.snapshot(BASE)
    new = watch.snapshot({**BASE, "version": 2, "fields": BASE["fields"] + [
        {"id": "extra", "label": "新字段", "type": "text", "required": False},
    ]})
    summary = watch.change_summary(old, new)
    assert summary is not None
    assert "版本 1→2" in summary
    assert "字段数 2→3" in summary


def test_change_summary_falls_back_to_structure_change():
    old = watch.snapshot(BASE)
    new = watch.snapshot({**BASE, "fields": [
        {**BASE["fields"][0], "type": "text"}, BASE["fields"][1],
    ]})
    summary = watch.change_summary(old, new)
    assert summary == "字段结构有变化"


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


def test_first_run_records_and_second_notifies_admin(monkeypatch):
    engine = _engine()
    with Session(engine) as raw:
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

        templates = [dict(BASE)]
        monkeypatch.setattr(watch.client, "configured", lambda: True)
        monkeypatch.setattr(watch.client, "list_templates", AsyncMock(return_value=templates))
        monkeypatch.setattr(app_settings, "oa_sample_template_code", BASE["code"])
        monkeypatch.setattr(app_settings, "oa_sample_template_id", "")
        monkeypatch.setattr(app_settings, "oa_contract_template_code", "")
        monkeypatch.setattr(app_settings, "oa_contract_template_id", "")
        monkeypatch.setattr(app_settings, "oa_inquiry_template_code", "")
        monkeypatch.setattr(app_settings, "oa_inquiry_template_id", "")

        asyncio.run(watch.check_all(SyncSessionAsAsync(raw)))
        raw.commit()
        assert raw.query(SystemSetting).count() == 1
        assert raw.query(Notification).count() == 0

        templates[0] = {
            **BASE,
            "version": 2,
            "fields": BASE["fields"] + [
                {"id": "extra", "label": "新字段", "type": "text", "required": False},
            ],
        }
        asyncio.run(watch.check_all(SyncSessionAsAsync(raw)))
        raw.commit()
        notes = raw.query(Notification).all()
        assert len(notes) == 1
        assert notes[0].user_id == 1
        assert notes[0].title == "OA 打样模板已变更"
        assert "版本 1→2" in notes[0].content
        assert "字段数 2→3" in notes[0].content

