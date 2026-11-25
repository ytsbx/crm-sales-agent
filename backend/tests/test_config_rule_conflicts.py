"""Configuration write paths use a disposable SQLite database."""

from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.audit import AuditLog
from app.core.errors import AppError
from app.modules.pricing import router as pricing
from app.modules.pricing.model import LogisticsRate
from app.modules.settings import router as settings
from app.modules.settings.model import PublicPoolRule, SystemSetting, TaskRule
from app.modules.user.model import Role
from tests.conftest import SyncSessionAsAsync, make_engine


@pytest.fixture
def db():
    engine = make_engine()
    for model in (AuditLog, LogisticsRate, PublicPoolRule, Role, SystemSetting, TaskRule):
        model.__table__.create(engine, checkfirst=True)
    with Session(engine) as raw:
        yield raw, (Request({"type": "http", "headers": []}), SimpleNamespace(id=1), SyncSessionAsAsync(raw))
    engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("second_cap", [200, 500, "500.00"])
async def test_approval_amount_ranges_must_increase(db, second_cap):
    _, args = db
    payload = settings.SettingInput(key="approval_levels", value={"levels": [
        {"max_amount": 500}, {"max_amount": second_cap}, {"max_amount": None},
    ]})
    with pytest.raises(AppError, match="第 2 档金额上限必须大于上一档"):
        await settings.upsert_setting(payload, *args)
    payload.value["levels"][1]["max_amount"] = 501
    await settings.upsert_setting(payload, *args)


@pytest.mark.anyio
async def test_approval_levels_validate_roles_and_amount_at_save(db):
    raw, args = db
    raw.add(Role(code="reviewer", name="报价审核"))
    raw.commit()
    value = {"levels": [
        {"max_amount": "99999999999999.98", "role_codes": ["reviewer"]},
        {"max_amount": "99999999999999.99", "role_codes": []},
        {"max_amount": None},
    ]}
    payload = settings.SettingInput(key="approval_levels", value=value)
    await settings.upsert_setting(payload, *args)
    for change, message in [
        ({"max_amount": "9" * 310}, "金额上限超过报价金额"),
        ({"role_codes": ["missing_role"]}, "审批角色.*不存在"),
        ({"role_codes": [123]}, "必须是一组角色码"),
    ]:
        invalid = {"levels": [{**value["levels"][0], **change}, *value["levels"][1:]]}
        with pytest.raises(AppError, match=message):
            await settings.upsert_setting(settings.SettingInput(key=payload.key, value=invalid), *args)
    assert raw.query(SystemSetting).filter_by(key=payload.key).one().value == value


@pytest.mark.anyio
async def test_pool_duplicate_create_enable_and_unrated_entry(db):
    raw, args = db
    await settings.create_pool_rule(settings.PublicPoolRuleInput(level="A", days=30), *args)
    with pytest.raises(AppError, match="30 天回收规则"):
        await settings.create_pool_rule(settings.PublicPoolRuleInput(level="A", days=7), *args)
    await settings.create_pool_rule(settings.PublicPoolRuleInput(level="A", days=7, enabled=False), *args)
    active, inactive = raw.query(PublicPoolRule).order_by(PublicPoolRule.id).all()
    with pytest.raises(AppError, match="30 天回收规则"):
        await settings.update_pool_rule(inactive.id, settings.PublicPoolRuleUpdate(enabled=True), *args)
    assert not inactive.enabled
    await settings.update_pool_rule(active.id, settings.PublicPoolRuleUpdate(enabled=False), *args)
    await settings.update_pool_rule(inactive.id, settings.PublicPoolRuleUpdate(enabled=True), *args)
    await settings.save_unrated_rules(settings.UnratedRulesInput(recycle_days=10, recycle_enabled=True), *args)
    with pytest.raises(AppError, match="10 天回收规则"):
        await settings.update_pool_rule(inactive.id, settings.PublicPoolRuleUpdate(level="UNRATED"), *args)


@pytest.mark.anyio
async def test_task_conflict_requires_same_title_and_overlapping_scope(db):
    raw, args = db
    def payload(code, levels, days, name="客户久未联系", status="active"):
        return settings.TaskRuleInput(code=code, name=name, trigger_type="customer_silent",
                                      trigger_config={"levels": levels, "days": days}, status=status)
    await settings.create_task_rule(payload("a", ["A"], 7), *args)
    with pytest.raises(AppError, match="同名任务"):
        await settings.create_task_rule(payload("overlap", ["A", "B"], 10), *args)
    await settings.create_task_rule(payload("b", ["B"], 10), *args)
    await settings.create_task_rule(payload("stage", ["A"], 10, name="主管二次提醒"), *args)
    await settings.create_task_rule(payload("draft", ["A"], 10, status="inactive"), *args)
    draft = raw.query(TaskRule).filter_by(code="draft").one()
    with pytest.raises(AppError, match="同名任务"):
        await settings.update_task_rule(draft.id, settings.TaskRuleUpdate(status="active"), *args)
    await settings.create_task_rule(payload("unrated", ["UNRATED"], 10,
                                          name="未分级客户久未联系，请跟进并了解客户后评级"), *args)
    with pytest.raises(AppError, match="同名任务"):
        await settings.save_unrated_rules(settings.UnratedRulesInput(reminder_days=7, reminder_enabled=True), *args)


@pytest.mark.anyio
async def test_logistics_conflict_keeps_specific_and_generic_fallback(db):
    raw, args = db
    def payload(origin=None, destination=None):
        return pricing.LogisticsRateCreate(provider="测试物流", origin_region=origin, destination_region=destination)
    await pricing.create_logistics_rate(payload(), *args)
    await pricing.create_logistics_rate(payload(origin="宁波"), *args)
    await pricing.create_logistics_rate(payload(origin="宁波", destination="天津"), *args)
    with pytest.raises(AppError, match="路线与已启用费率"):
        await pricing.create_logistics_rate(payload(origin="宁波", destination="天津"), *args)
    with pytest.raises(AppError, match="路线与已启用费率"):
        await pricing.create_logistics_rate(payload(destination="天津"), *args)
    rate = raw.query(LogisticsRate).filter_by(origin_region="宁波", destination_region="天津").one()
    await pricing.update_logistics_rate(rate.id, pricing.LogisticsRateUpdate(status="inactive"), *args)
    await pricing.create_logistics_rate(payload(origin="宁波", destination="天津"), *args)
    with pytest.raises(AppError, match="路线与已启用费率"):
        await pricing.update_logistics_rate(rate.id, pricing.LogisticsRateUpdate(status="active"), *args)
    assert rate.status == "inactive"
