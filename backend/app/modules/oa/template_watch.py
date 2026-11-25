"""OA 模板变更提醒：表单字段结构变了，给管理员发一条站内通知。

为什么只提醒、不自动适配：CRM 的业务字段（客户、产品、数量…）必须知道
从哪来，模板新增/改名字段时没法凭空推断。这里只做变更检测，把"OA 表单变了"
尽早告诉管理员，并点名受影响的映射，由人去改配置/映射。

审批人单独处理：native 路径 CRM 不传审批人，换审批人由同序负责，不需要提醒；
但旧/钉钉身份路径会用 OA_*_APPROVER_* 覆盖审批人，这时模板改了审批人不会生效，
属于真实盲区，需要提醒管理员。

实现：字段结构和审批路线各算一个指纹存进 system_settings；字段变化时通知
admin 并点名缺失/新增字段；审批路线变化仅在 CRM 会覆盖审批人时提醒。
首次见到只存不报；模板回包字段为空时跳过，避免把异常回包记成"0 字段模板"。
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


def _options_hash(options) -> str:
    raw = json.dumps(options or [], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _field_signature(field: dict) -> dict:
    return {
        "id": field.get("id"),
        "label": field.get("label"),
        "type": field.get("type"),
        "required": bool(field.get("required")),
        "options_hash": _options_hash(field.get("options")),
    }


def normalize(template: dict) -> dict:
    """只保留 CRM 要映射的表单字段结构；审批人单独算指纹。"""
    return {"fields": [_field_signature(field) for field in template.get("fields") or []]}


def fingerprint(template: dict) -> str:
    raw = json.dumps(normalize(template), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def approver_fingerprint(template: dict) -> str:
    raw = json.dumps(template.get("routing") or {}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def snapshot(template: dict) -> dict:
    return {
        "hash": fingerprint(template),
        "approver_hash": approver_fingerprint(template),
        "code": template.get("code") or "",
        "name": template.get("name") or "",
        "version": template.get("version"),
        "field_count": len(template.get("fields") or []),
        "fields": normalize(template)["fields"],
        "checked_at": datetime.now(UTC).isoformat(),
    }


def _field_map(value: dict | None) -> dict[str, dict]:
    return {
        field["id"]: field
        for field in (value or {}).get("fields") or []
        if isinstance(field, dict) and field.get("id")
    }


def _names(ids: list[str], mapping: dict[str, dict]) -> str:
    parts = []
    for identity in ids:
        label = (mapping.get(identity) or {}).get("label")
        parts.append(f"{identity}（{label}）" if label else identity)
    return "、".join(parts)


def expected_field_ids(kind: str) -> set[str]:
    """CRM 当前会读取/预填的 OA 字段 id（按模块现有映射表取，不另抄一份）。"""
    if kind == "sample":
        from app.modules.oa.sample import SOURCE_IDS

        return set(SOURCE_IDS) | {"sample_type", "product_category"}
    if kind == "inquiry":
        from app.modules.oa.inquiry import NATIVE_ENTRY_FIELD_IDS, NATIVE_FIELDS

        return (set(NATIVE_ENTRY_FIELD_IDS) | set(NATIVE_FIELDS.values())
                | {"reporter", "report_department", "quoter", "reference_images", "quantity"})
    if kind == "contract":
        from app.modules.oa.contract_form import SOURCE_FIELDS, LOCKED

        return set(SOURCE_FIELDS.values()) | {SOURCE_FIELDS.get(key, key) for key in LOCKED}
    return set()


def field_change_summary(old: dict | None, new: dict, expected_ids: set[str]) -> str | None:
    """把变更说成人能直接动手的句子：哪些字段没了、哪些新增没映射、哪些属性变了。"""
    if old is None or old.get("hash") == new.get("hash"):
        return None
    old_fields = _field_map(old)
    new_fields = _field_map(new)
    if not old_fields:
        return f"字段数 {old.get('field_count')}→{new.get('field_count')}"

    old_ids, new_ids = set(old_fields), set(new_fields)
    removed = old_ids - new_ids
    removed_expected = sorted(removed & expected_ids)
    removed_other = sorted(removed - expected_ids)
    added = new_ids - old_ids
    added_unmapped = sorted(added - expected_ids)
    added_mapped = sorted(added & expected_ids)

    changed = []
    for identity in sorted(old_ids & new_ids):
        before, after = old_fields[identity], new_fields[identity]
        bits = []
        if before.get("label") != after.get("label"):
            bits.append(f"标签「{before.get('label')}」→「{after.get('label')}」")
        if before.get("required") != after.get("required"):
            bits.append("改为必填" if after.get("required") else "取消必填")
        if before.get("type") != after.get("type"):
            bits.append(f"类型 {before.get('type')}→{after.get('type')}")
        if before.get("options_hash") != after.get("options_hash"):
            bits.append("选项有变化")
        if bits:
            changed.append(f"{identity}（{after.get('label')}）：{'，'.join(bits)}")

    parts = []
    if old.get("version") != new.get("version"):
        parts.append(f"版本 {old.get('version')}→{new.get('version')}")
    if removed_expected:
        parts.append("新模板里找不到 CRM 需要的字段：" + _names(removed_expected, old_fields))
    if removed_other:
        parts.append("删除字段：" + _names(removed_other, old_fields))
    if added_unmapped:
        parts.append("新增未映射字段：" + _names(added_unmapped, new_fields))
    if added_mapped:
        parts.append("新增/补回 CRM 字段：" + _names(added_mapped, new_fields))
    if changed:
        parts.append("字段变化：" + "；".join(changed[:3]) + ("…" if len(changed) > 3 else ""))
    if not parts:
        parts.append("字段结构有变化")
    return "。".join(parts)


def approver_override(kind: str) -> tuple[bool, str]:
    """CRM 当前是否会用自己的配置覆盖模板审批人。"""
    if kind == "sample":
        return (bool(settings.oa_sample_approver_dingtalk_ids.strip()),
                "OA_SAMPLE_APPROVER_DINGTALK_IDS")
    if kind == "inquiry":
        native = (not settings.oa_inquiry_template_id.strip()
                  and settings.oa_inquiry_template_code.strip() == "key-account-product-inquiry")
        return (not native and bool(settings.oa_inquiry_approver_dingtalk_id.strip()),
                "OA_INQUIRY_APPROVER_DINGTALK_ID")
    if kind == "contract":
        return (bool(settings.oa_contract_approver_dingtalk_id.strip()),
                "OA_CONTRACT_APPROVER_DINGTALK_ID")
    return False, ""


def approver_change_message(kind: str) -> str:
    override, env = approver_override(kind)
    if not override:
        return ""
    return (f"模板审批路线/审批人有变化，但 CRM 当前会按 {env} 推送审批人"
            "（旧/钉钉身份），实际审批人可能不变。要让模板生效，请清空该配置。")


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
    """检查一个模板；返回是否发生了需要管理员处理的变更并已通知。"""
    code = template.get("code") or template.get("id") or "unknown"
    if not template.get("fields"):
        logger.warning("OA 模板 %s 回包字段为空，跳过本次记录", code)
        return False

    key = setting_key(kind, str(code))
    old = await _load_snapshot(session, key)
    new = snapshot(template)
    field_message = field_change_summary(old, new, expected_field_ids(kind)) if old else None
    approver_message = ""
    if old and old.get("approver_hash") != new["approver_hash"]:
        approver_message = approver_change_message(kind)
        if not approver_message:
            logger.info("OA %s 模板审批人变化，但 CRM 不覆盖，无需提醒", kind)

    messages = [message for message in (field_message, approver_message) if message]
    if messages:
        label = KINDS.get(kind, (kind, "", ""))[0]
        if field_message and approver_message:
            title = f"OA {label}模板已变更"
        elif approver_message:
            title = f"OA {label}模板审批人配置有冲突"
        else:
            title = f"OA {label}模板字段已变更"
        await notification_service.notify_roles(
            session,
            role_codes=["admin"],
            type_="system",
            title=title,
            content=f"{new.get('name') or code}：{'；'.join(messages)}",
            business_type="oa_template",
        )

    await _save_snapshot(session, key, new)
    return bool(messages)


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
