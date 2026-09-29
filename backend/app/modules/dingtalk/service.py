"""询价审批的发起与结果回收（文档 §11.3 :152 / 场景11）。

链路：CRM 需求版本 → 发起钉钉审批实例 → 拿回实例 ID → 回收状态与结果 → 回到那条需求。

## 两条纪律

**1. 幂等靠业务键，不靠实例 ID。**
场景11 要求"不重复建 OA 单"。重复提交时我们**压根不该再向钉钉要一次实例**——
等实例 ID 回来再判重，单子已经建出去了。所以先用
（需求 + 需求版本 + OA 类型）查 `oa_instances`，命中就直接返回既有行；
真并发时还有唯一约束兜底（IntegrityError 里回查一次）。

**2. 表单值用控件 id，不是控件名称。**
钉钉的 `formValues` 以控件 id 为 key；名字对不上时**钉钉不报错，只把那格留空**。
于是"预填成功"的假象下业务还得手填一遍，正好把"免重复录入"这条验收标准踩没。
所以 `build_form_values` 只认调用方给的字段映射，映射从模板字段清单来
（见 17-交接说明 §6 的待外部输入）。
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.modules.dingtalk.client import DingTalkError, get_client
from app.modules.dingtalk.model import OA_STATUS_LABEL, OaInstance

#: 钉钉的审批状态词 → CRM 侧口径。CRM 侧固定这五个值，
#: 前端和查询都不用认对方系统的用词（与 ERP Adapter 同一套做法：翻译只发生在一处）。
_STATUS_MAP = {
    "RUNNING": "pending",
    "COMPLETED": "approved",
    "TERMINATED": "rejected",
    "CANCELED": "withdrawn",
}


def build_form_values(field_map: dict[str, Any]) -> list[dict[str, Any]]:
    """把 CRM 字段拼成钉钉的 `formValues`。

    `field_map` 的 key 必须是**模板控件的 id**（不是中文名）。
    空值**不提交**——钉钉对空值控件会覆盖已有内容，把"没填"当成"清空"，
    这在"驳回后重提"时会把上一次填的内容抹掉。
    """
    return [
        {"name": key, "value": value}
        for key, value in field_map.items()
        if value not in (None, "", [], {})
    ]


async def get_by_business_key(
    session: AsyncSession, *, inquiry_id: int, inquiry_version: int, oa_type: str
) -> OaInstance | None:
    return (
        await session.execute(
            select(OaInstance).where(
                OaInstance.inquiry_id == inquiry_id,
                OaInstance.inquiry_version == inquiry_version,
                OaInstance.oa_type == oa_type,
            )
        )
    ).scalars().first()


async def create_inquiry_instance(
    session: AsyncSession,
    *,
    user: CurrentUser,
    inquiry_id: int,
    inquiry_version: int,
    customer_id: int | None,
    process_code: str,
    originator_user_id: str,
    field_map: dict[str, Any],
    oa_type: str = "inquiry",
) -> OaInstance:
    """发起询价审批（幂等）。已发起过就返回既有行，不再打钉钉。"""
    existing = await get_by_business_key(
        session, inquiry_id=inquiry_id, inquiry_version=inquiry_version, oa_type=oa_type
    )
    if existing is not None:
        # 已发起（哪怕是失败）都先返回：失败的那行要人工决定"重发还是改需求重提"，
        # 自动重发可能把同一个需求在钉钉里建出两张单——正是场景11 要防的
        return existing

    form_values = build_form_values(field_map)
    from app.core.config import settings as app_settings

    # 推送总闸（默认关）：测试期绝不向外部系统发起真实审批单。
    # 与企微同一套语义——记 skipped 并写明原因，"没发"不等于"发失败"。
    if app_settings.dingtalk_push_off:
        blocked = OaInstance(
            customer_id=customer_id,
            inquiry_id=inquiry_id,
            inquiry_version=inquiry_version,
            oa_type=oa_type,
            process_code=process_code,
            originator_user_id=originator_user_id,
            form_snapshot={"formValues": form_values},
            status="skipped",
            error="钉钉推送已关闭（DINGTALK_PUSH_OFF），未向钉钉发起审批",
            created_by=user.id,
            created_at=datetime.now(UTC),
        )
        session.add(blocked)
        await session.flush()
        return blocked

    row = OaInstance(
        customer_id=customer_id,
        inquiry_id=inquiry_id,
        inquiry_version=inquiry_version,
        oa_type=oa_type,
        process_code=process_code,
        originator_user_id=originator_user_id,
        form_snapshot={"formValues": form_values},
        status="pending",
        created_by=user.id,
        created_at=datetime.now(UTC),
    )
    try:
        instance_id = await get_client().create_process_instance(
            process_code=process_code,
            form_values=form_values,
            originator_user_id=originator_user_id,
        )
    except Exception as exc:  # 配置缺失/网络/钉钉业务错误：都落痕，不假装成功
        row.status = "failed"
        row.error = f"{type(exc).__name__}: {exc}"[:500]
        session.add(row)
        await session.flush()
        return row

    row.instance_id = instance_id
    row.synced_at = datetime.now(UTC)
    session.add(row)
    await session.flush()
    return row


async def sync_pending_instances(session: AsyncSession, *, limit: int = 50) -> dict:
    """轮询回写：把还在审批中的实例拉一次状态与结果。

    为什么先做轮询而不是事件订阅：轮询**不需要公网回调地址、也不需要管理员
    在 OA 后台额外授权**，而且天然不会丢消息——这一轮没查到，下一轮还会查。
    代价只是延迟几分钟，而询价审批本来就要几小时到几天。
    """
    rows = (
        await session.execute(
            select(OaInstance)
            .where(OaInstance.status == "pending", OaInstance.instance_id.is_not(None))
            .order_by(OaInstance.id.asc())
            .limit(limit)
        )
    ).scalars().all()
    if not rows:
        return {"checked": 0, "changed": 0}

    client = get_client()
    changed = 0
    for row in rows:
        try:
            data = await client.get_process_instance(row.instance_id or "")
        except DingTalkError as exc:
            # 单条查失败不影响其他单：记在行上，下一轮还会再试
            row.error = str(exc)[:500]
            continue
        raw = str(data.get("status") or data.get("result") or "")
        new_status = _STATUS_MAP.get(raw.upper())
        row.result = {"raw_status": raw, "payload": data}
        row.synced_at = datetime.now(UTC)
        row.updated_at = datetime.now(UTC)
        if new_status and new_status != row.status:
            row.status = new_status
            changed += 1
    await session.flush()
    return {"checked": len(rows), "changed": changed}


def serialize(row: OaInstance) -> dict:
    return {
        "id": row.id,
        "inquiry_id": row.inquiry_id,
        "inquiry_version": row.inquiry_version,
        "oa_type": row.oa_type,
        "instance_id": row.instance_id,
        "status": row.status,
        "status_label": OA_STATUS_LABEL.get(row.status, row.status),
        "result": row.result,
        "error": row.error,
        "synced_at": row.synced_at.isoformat() if row.synced_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
