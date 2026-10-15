"""审批流转规则的配置接口（设计稿 `_6`：规则列表 / 编辑发布 / 版本 / 沙盒试算）。

路由注册顺序（本项目踩过三次的坑）：静态路径 `/approval-rules/sandbox`
必须注册在动态路径 `/approval-rules/{rule_id}` **之前**。
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok
from app.modules.approval import rules_engine
from app.modules.approval.model import ApprovalRule, ApprovalRuleVersion
from app.modules.quote import service as quote_service

router = APIRouter(tags=["ApprovalRules"])


class RuleCondition(BaseModel):
    field: str
    op: str = "gte"
    value: object = None


class RulePayload(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: str
    priority: int = 100
    enabled: bool = False
    conditions: list[RuleCondition] = Field(default_factory=list, min_length=1)
    action: dict = Field(default_factory=dict)
    description: str | None = None


class RuleToggle(BaseModel):
    enabled: bool


class SandboxIn(BaseModel):
    quote_version_id: int


def _validate_payload(payload: RulePayload) -> None:
    """发布前校验：kind 合法、条件字段与操作符在目录内、值类型匹配。"""
    if payload.kind not in rules_engine.VALID_KINDS:
        raise AppError(ErrorCode.PARAM_ERROR, f"未知规则类型 {payload.kind}", 422)
    meta = {f["field"]: f for f in rules_engine.CONTEXT_FIELDS}
    for cond in payload.conditions:
        info = meta.get(cond.field)
        if info is None:
            raise AppError(ErrorCode.PARAM_ERROR, f"未知条件字段 {cond.field}", 422)
        if cond.op not in info["ops"]:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"字段「{info['label']}」不支持操作符 {cond.op}，可用：{'/'.join(info['ops'])}",
                422,
            )
        if info["value_type"] == "number" and not isinstance(cond.value, (int, float)):
            raise AppError(ErrorCode.PARAM_ERROR, f"「{info['label']}」的阈值必须是数字", 422)
        if info["value_type"] == "bool" and not isinstance(cond.value, bool):
            raise AppError(ErrorCode.PARAM_ERROR, f"「{info['label']}」的阈值必须是 true/false", 422)
    if payload.kind == "exception_route":
        # 旧规则中的角色字段可读取以兼容历史版本，但不再决定谁能会签。
        permission = payload.action.get("add_node_permission", "quote:finance_approve")
        if permission != "quote:finance_approve":
            raise AppError(ErrorCode.PARAM_ERROR, "异常会签使用独立的财务会签权限", 422)


def _snapshot(rule: ApprovalRule) -> dict:
    return {
        "name": rule.name,
        "kind": rule.kind,
        "priority": rule.priority,
        "conditions": rule.conditions,
        "action": rule.action,
        "description": rule.description,
    }


async def _get_rule(session: AsyncSession, rule_id: int) -> ApprovalRule:
    rule = await session.get(ApprovalRule, rule_id)
    if rule is None:
        raise AppError(ErrorCode.NOT_FOUND, "规则不存在", 404)
    return rule


async def _has_draft_changes(session: AsyncSession, rule: ApprovalRule) -> bool:
    if rule.published_version_no == 0:
        return True  # 从未发布过：草稿就是"未生效"状态
    snapshot = (
        await session.execute(
            select(ApprovalRuleVersion)
            .where(
                ApprovalRuleVersion.rule_id == rule.id,
                ApprovalRuleVersion.version_no == rule.published_version_no,
            )
            .limit(1)
        )
    ).scalars().first()
    if snapshot is None:
        return True
    return _snapshot(rule) != snapshot.payload


@router.get("/approval-rules/condition-fields")
async def condition_fields(
    _: CurrentUser = Depends(require_permission("quote:view")),
):
    """条件字段目录：前端编辑器的字段/操作符下拉、单位与提示语都来自这里。"""
    return ok(rules_engine.CONTEXT_FIELDS)


@router.get("/approval-rules")
async def list_rules(
    _: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    rows = (
        await session.execute(
            select(ApprovalRule).order_by(ApprovalRule.priority.asc(), ApprovalRule.id.asc())
        )
    ).scalars().all()
    items = []
    for row in rows:
        items.append(
            rules_engine.serialize_rule(
                row, has_draft_changes=await _has_draft_changes(session, row)
            )
        )
    return ok(items)


@router.post("/approval-rules")
async def create_rule(
    payload: RulePayload,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    _validate_payload(payload)
    rule = ApprovalRule(
        name=payload.name,
        kind=payload.kind,
        priority=payload.priority,
        enabled=False,  # 新建一律先停用，发布后再打开
        conditions=[c.model_dump() for c in payload.conditions],
        action=payload.action,
        description=payload.description,
    )
    session.add(rule)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="approval_rule",
        business_id=rule.id,
        after=rules_engine.serialize_rule(rule),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(rules_engine.serialize_rule(rule), "规则已创建（草稿），发布后生效")


@router.post("/approval-rules/sandbox")
async def sandbox(
    payload: SandboxIn,
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    """规则沙盒：拿一张真实报价版本试跑全部规则，逐条给出命中明细，不产生任何副作用。

    试算对象是**当前草稿状态**——正好用于"改完规则、发布前先验证"这个动作。
    """
    # 必须与读报价详情同口径走数据范围：沙盒把 context 全量回给调用方，
    # 里面有总额、毛利、最低明细毛利、客户等级与逾期状态。此前只判"版本存在"，
    # 任何有 quote:view 的人枚举 quote_version_id 就能读到别人的报价与毛利。
    version = await quote_service.get_visible_version(
        session, user, payload.quote_version_id
    )
    quote = await quote_service.get_visible_quote(session, user, version.quote_id)
    items = await quote_service.version_items(session, version.id)
    ctx = await rules_engine.build_context(
        session, quote=quote, version=version, items=items, fx=version.exchange_rate_snapshot
    )

    rules = (
        await session.execute(
            select(ApprovalRule).order_by(ApprovalRule.priority.asc(), ApprovalRule.id.asc())
        )
    ).scalars().all()
    results = []
    fired: int | None = None
    for rule in rules:
        matched, detail = rules_engine.evaluate_conditions(rule.conditions or [], ctx)
        # ⚠️ **只有启用且已发布的规则才算"会被执行"**（第十批审查 ⑪，2026-10-13）。
        #
        # 实际提交走的是 `rules_engine.load_active_rules`：
        # `enabled.is_(True) AND published_version_no > 0`。
        # 这里原来只看"条件命中"，于是：一条**已停用**的高优先免审规则会让沙盒
        # 显示"免审，无需人工审批"，而实际提交却进主管审批 —— 沙盒把人骗了。
        #
        # 停用/未发布的规则继续**展示条件试算**（沙盒的本来用途，含未发布的启用草稿），
        # 只是不能当"已执行的规则"。
        executable = bool(rule.enabled) and int(rule.published_version_no or 0) > 0
        if matched and fired is None and executable:
            fired = rule.id
        results.append(
            {
                "rule_id": rule.id,
                "name": rule.name,
                "kind": rule.kind,
                "priority": rule.priority,
                "enabled": rule.enabled,
                "published_version_no": rule.published_version_no,
                "matched": matched,
                "executable": executable,
                "fired": fired == rule.id,
                "effect": rules_engine.effect_summary(rule.kind, rule.action) if matched else None,
                "conditions_detail": rules_engine._cond_detail_pretty(detail),
            }
        )
    # ⚠️ **按权限脱敏**（第十批审查 ③，2026-10-13）。
    #
    # 这个返回里有 `gross_margin` / `min_item_margin`（由成本推出）与
    # `total_amount`（售价）。只给 `quote:view`、没有 `price:manage` 的人
    # 拿到这两个就能反推成本：实测售价 100、毛利率 80% → 成本 20。
    # `conditions_detail` 里的 `actual` 同样是毛利率的实际值，一起擦。
    #
    # 报告点名的四类泄漏都要处理，只删一个字段没用；这里覆盖 ①②，
    # ③（成本派生底价）在报价侧由 `quote.service.strip_comparison_cost` 管，
    # ④（文字原因）见下面 `strip_cost_reason`。
    # 用项目既有的判权限 API（），别自己去翻属性名
    can_see_cost = user.has("price:manage")
    safe_ctx = rules_engine.strip_cost_context(ctx, can_see_cost=can_see_cost)
    safe_results = results
    if not can_see_cost:
        safe_results = [
            {
                **r,
                "conditions_detail": [
                    ({**row, "actual": None, "actual_label": "无权限查看"}
                     if row.get("field") in rules_engine.COST_SENSITIVE_SET else row)
                    for row in (r.get("conditions_detail") or [])
                ],
            }
            for r in results
        ]
    return ok(
        {
            "context": safe_ctx,
            "context_fields": rules_engine.CONTEXT_FIELDS,
            "cost_fields_hidden": not can_see_cost,
            "rules": safe_results,
            "fired_rule_id": fired,
            "fired_effect": next(
                (r["effect"] for r in safe_results if r["fired"]), None
            ),
        }
    )


@router.patch("/approval-rules/{rule_id}")
async def update_rule(
    rule_id: int,
    payload: RulePayload,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """编辑草稿。改动**不立即生效**——引擎只按已发布版本求值，要生效请发布。"""
    rule = await _get_rule(session, rule_id)
    _validate_payload(payload)
    before = rules_engine.serialize_rule(rule)
    rule.name = payload.name
    rule.kind = payload.kind
    rule.priority = payload.priority
    rule.conditions = [c.model_dump() for c in payload.conditions]
    rule.action = payload.action
    rule.description = payload.description
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="approval_rule",
        business_id=rule.id,
        before=before,
        after=rules_engine.serialize_rule(rule),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        rules_engine.serialize_rule(
            rule, has_draft_changes=await _has_draft_changes(session, rule)
        ),
        "草稿已保存，发布后生效",
    )


@router.patch("/approval-rules/{rule_id}/enabled")
async def toggle_rule(
    rule_id: int,
    payload: RuleToggle,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """启停开关：立即生效的运维动作（与草稿/发布无关）。

    未发布过的规则打开开关也**不会**生效——没有已发布版本就没有可求值的内容。
    """
    rule = await _get_rule(session, rule_id)
    if payload.enabled and rule.published_version_no == 0:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED, "该规则从未发布过，请先发布再启用", 422
        )
    before = rule.enabled
    rule.enabled = payload.enabled
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="toggle",
        business_type="approval_rule",
        business_id=rule.id,
        before={"enabled": before},
        after={"enabled": rule.enabled},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(rules_engine.serialize_rule(rule), "已启用" if rule.enabled else "已停用")


@router.post("/approval-rules/{rule_id}/publish")
async def publish_rule(
    rule_id: int,
    request: Request,
    confirm: bool = False,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """把当前草稿发布成新版本：生成不可变快照，引擎从此按这一版求值。

    ⚠️ **必须先确认**（审查 2026-10-10）：发布之后**后续所有提交的报价审批
    都按新规则算**，会直接改变"哪些报价要审批、走哪一级"。
    所以先让用户看清这一版规则的关键条件再发。
    """
    rule = await _get_rule(session, rule_id)
    if not confirm:
        from app.core.confirmation import confirmation_message

        from app.modules.approval.rules_engine import describe_conditions

        detail = "；".join(
            [
                f"规则「{rule.name}」（{rule.kind}，优先级 {rule.priority}、"
                f"{'启用' if rule.enabled else '停用'}）",
                "条件：" + (describe_conditions(rule.conditions or []) or "（无条件）"),
                f"将发布为 V{rule.published_version_no + 1}，"
                "**之后提交的报价审批按新规则计算**",
                "已提交/已审批的历史报价不受影响",
            ]
        )
        raise AppError(
            ErrorCode.CONFIRM_REQUIRED,
            confirmation_message(action="发布审批规则", detail=detail),
            422,
        )
    rule.conditions = rule.conditions or []
    _validate_payload(
        RulePayload(
            name=rule.name,
            kind=rule.kind,
            priority=rule.priority,
            enabled=rule.enabled,
            conditions=[RuleCondition(**c) for c in rule.conditions],
            action=rule.action or {},
            description=rule.description,
        )
    )
    new_no = rule.published_version_no + 1
    session.add(
        ApprovalRuleVersion(
            rule_id=rule.id,
            version_no=new_no,
            payload=_snapshot(rule),
            published_by=user.id,
        )
    )
    rule.published_version_no = new_no
    rule.published_at = datetime.now(UTC)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="publish",
        business_type="approval_rule",
        business_id=rule.id,
        after={"version_no": new_no, "name": rule.name},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(rules_engine.serialize_rule(rule), f"已发布 V{new_no}，命中该规则的单据将按新版本路由")


@router.get("/approval-rules/{rule_id}/versions")
async def rule_versions(
    rule_id: int,
    _: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    rule = await _get_rule(session, rule_id)
    rows = (
        await session.execute(
            select(ApprovalRuleVersion)
            .where(ApprovalRuleVersion.rule_id == rule.id)
            .order_by(ApprovalRuleVersion.version_no.desc())
        )
    ).scalars().all()
    return ok(
        [
            {
                "id": row.id,
                "version_no": row.version_no,
                "payload": row.payload,
                "published_by": row.published_by,
                "published_at": row.published_at,
                "is_current": row.version_no == rule.published_version_no,
            }
            for row in rows
        ]
    )


@router.delete("/approval-rules/{rule_id}")
async def delete_rule(
    rule_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """删除规则。审批单上的规则痕迹存的是名称快照，删规则不影响历史留痕。"""
    rule = await _get_rule(session, rule_id)
    before = rules_engine.serialize_rule(rule)
    # 先删子表再删父表：ORM 的单元OfWork不保证无关对象的删除顺序，
    # 直接 delete(rule) 会撞 approval_rule_versions 的外键
    from sqlalchemy import delete

    await session.execute(delete(ApprovalRuleVersion).where(ApprovalRuleVersion.rule_id == rule.id))
    await session.delete(rule)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="approval_rule",
        business_id=rule_id,
        before=before,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "规则已删除")
