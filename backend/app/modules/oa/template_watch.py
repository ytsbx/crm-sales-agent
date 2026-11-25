"""OA 模板变更提醒：表单字段结构变了，给管理员发一条站内通知。

为什么只提醒、不自动适配：CRM 的业务字段（客户、产品、数量…）必须知道
从哪来，模板新增一个字段时没法凭空推断。这里只做变更检测，把"OA 表单变了"
尽早告诉管理员，由人去核对 CRM 的字段映射。

审批人不在检查范围内：CRM 只发起审批、不同步审批人，换审批人由同序自己处理。

实现：把模板的字段结构做一次指纹存进 system_settings；下次指纹不同就通知
admin 并更新指纹。首次见到只存不报，避免上线时把现有模板当成"变更"刷屏。
"""
import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.modules.dingtalk.model import OaInstance
from app.modules.notification import service as notification_service
from app.modules.oa import client
from app.modules.settings.model import SystemSetting

logger = logging.getLogger("crm.oa.template_watch")

KINDS = {
    "contract": ("合同", "oa_contract_template_id", "oa_contract_template_code"),
    "sample": ("打样", "oa_sample_template_id", "oa_sample_template_code"),
    "inquiry": ("询价", "oa_inquiry_template_id", "oa_inquiry_template_code"),
}


def _field_signature(field: dict) -> dict:
    return {
        "id": field.get("id"),
        "label": field.get("label"),
        "type": field.get("type"),
        "required": bool(field.get("required")),
        "options": field.get("options") or [],
    }


def normalize(template: dict) -> dict:
    """只保留 CRM 真正要映射的表单字段结构。

    审批人和人员目录由同序负责：CRM 只发起、不同步审批人，换审批人
    不会影响 CRM，因此也不该拿来触发提醒。版本号同理，仅记录不比对。
    """
    return {"fields": [_field_signature(field) for field in template.get("fields") or []]}


def fingerprint(template: dict) -> str:
    raw = json.dumps(normalize(template), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def snapshot(template: dict) -> dict:
    return {
        "hash": fingerprint(template),
        "code": template.get("code") or "",
        "name": template.get("name") or "",
        "version": template.get("version"),
        "field_count": len(template.get("fields") or []),
        "checked_at": datetime.now(UTC).isoformat(),
    }


def change_summary(old: dict | None, new: dict) -> str | None:
    if not old or old.get("hash") == new["hash"]:
        return None
    parts = []
    if old.get("version") != new.get("version"):
        parts.append(f"版本 {old.get('version')}→{new.get('version')}")
    if old.get("field_count") != new.get("field_count"):
        parts.append(f"字段数 {old.get('field_count')}→{new.get('field_count')}")
    if not parts:
        parts.append("字段结构有变化")
    return "；".join(parts)


def pick_template(templates: list[dict], template_id: str, template_code: str) -> dict | None:
    if not template_id and not template_code:
        return None
    key = "id" if template_id else "code"
    wanted = template_id or template_code
    matches = [item for item in templates if item.get(key) == wanted]
    return max(matches, key=lambda item: item.get("version") or 0) if matches else None


def setting_key(kind: str, code: str) -> str:
    key = f"oa_tpl_fp:{kind}:{code}"
    if len(key) <= 64:
        return key
    digest = hashlib.sha1(code.encode("utf-8")).hexdigest()[:24]
    return f"oa_tpl_fp:{kind}:{digest}"


async def _load_snapshot(session, key: str) -> dict | None:
    row = (await session.execute(
        select(SystemSetting).where(SystemSetting.key == key)
    )).scalar_one_or_none()
    return row.value if row is not None and isinstance(row.value, dict) else None


async def _save_snapshot(session, key: str, value: dict) -> None:
    row = (await session.execute(
        select(SystemSetting).where(SystemSetting.key == key)
    )).scalar_one_or_none()
    if row is None:
        session.add(SystemSetting(
            key=key, value=value, description="OA 模板变更检测（系统自动维护）",
        ))
    else:
        row.value = value
        row.updated_at = datetime.now(UTC)


async def check_template(session, kind: str, template: dict) -> bool:
    """检查一个模板；返回是否发生了变更并已通知。"""
    code = template.get("code") or template.get("id") or "unknown"
    key = setting_key(kind, str(code))
    old = await _load_snapshot(session, key)
    new = snapshot(template)
    summary = change_summary(old, new)
    if old is not None and summary:
        label = KINDS.get(kind, (kind, "", ""))[0]
        await notification_service.notify_roles(
            session,
            role_codes=["admin"],
            type_="system",
            title=f"OA {label}模板已变更",
            content=f"{new.get('name') or code}：{summary}。请核对 CRM 的表单字段映射。",
            business_type="oa_template",
        )
    await _save_snapshot(session, key, new)
    return bool(old is not None and summary)


def _candidate_identity(value: str):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return str(value)


async def _candidate_identities(session) -> list:
    """最近提交过 OA 的人最可能仍有模板发起权限。"""
    recent = (await session.execute(
        select(OaInstance.originator_user_id)
        .where(OaInstance.originator_user_id.is_not(None))
        .order_by(OaInstance.created_at.desc())
        .limit(10)
    )).scalars().all()
    identities: list = []
    for value in recent:
        if value and value not in identities:
            identities.append(value)
    if identities:
        return identities
    from app.modules.user.model import User

    bound = (await session.execute(
        select(User.oa_user_id, User.dingtalk_userid)
        .where(User.status == "active")
        .where((User.oa_user_id.is_not(None)) | (User.dingtalk_userid.is_not(None)))
        .limit(5)
    )).all()
    for oa_user_id, dingtalk_userid in bound:
        value = oa_user_id or dingtalk_userid
        if value and value not in identities:
            identities.append(value)
    return identities


async def check_all(session) -> int:
    if not client.configured():
        return 0
    templates = None
    for identity in await _candidate_identities(session):
        try:
            rows = await client.list_templates(_candidate_identity(identity))
        except Exception as exc:
            logger.warning("读取 OA 模板失败（%s）：%s", identity, exc)
            continue
        if rows:
            templates = rows
            break
    if not templates:
        logger.warning("OA 模板检查跳过：没有可用于读取模板的申请身份")
        return 0
    changed = 0

    for kind, (_label, id_attr, code_attr) in KINDS.items():
        template = pick_template(
            templates,
            getattr(settings, id_attr).strip(),
            getattr(settings, code_attr).strip(),
        )
        if template is None:
            continue
        if await check_template(session, kind, template):
            changed += 1
    return changed


async def run_oa_template_watch_job() -> None:
    async with SessionLocal() as session:
        try:
            changed = await check_all(session)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("OA 模板检查失败")
            return
    logger.info("OA 模板检查完成：变更 %s 项", changed)
