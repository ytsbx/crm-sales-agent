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
from sqlalchemy.exc import IntegrityError
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
            .order_by(OaInstance.submit_round.desc())
        )
    ).scalars().first()


async def _next_round(
    session: AsyncSession, *, inquiry_id: int, inquiry_version: int, oa_type: str
) -> int:
    latest = await get_by_business_key(
        session, inquiry_id=inquiry_id, inquiry_version=inquiry_version, oa_type=oa_type
    )
    return int(latest.submit_round) + 1 if latest is not None else 1


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
    resubmit: bool = False,
) -> OaInstance:
    """发起询价审批。

    **提交轮次决定幂等键**（文档 :43 挡网络重试，§11.3 :152 要重提能跑通）：

    - `resubmit=False`（默认）——同一轮：已有记录就返回，不再打钉钉。
      网络重试、页面重复点击都命中这条，不会重复建单；
    - `resubmit=True`——驳回/撤销之后**业务主动重提**：轮次 +1、换幂等键，
      在钉钉里建一张新单。这条不这么做的话，重提会被唯一约束挡住、永远发不出去。
    """
    latest = await get_by_business_key(
        session, inquiry_id=inquiry_id, inquiry_version=inquiry_version, oa_type=oa_type
    )
    if latest is not None and not resubmit:
        return latest

    submit_round = (int(latest.submit_round) + 1) if (resubmit and latest) else 1
    idempotency_key = f"{inquiry_id}:{inquiry_version}:{oa_type}:{submit_round}"

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
            idempotency_key=idempotency_key,
            submit_round=submit_round,
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
        idempotency_key=idempotency_key,
        submit_round=submit_round,
        process_code=process_code,
        originator_user_id=originator_user_id,
        form_snapshot={"formValues": form_values},
        status="pending",
        created_by=user.id,
        created_at=datetime.now(UTC),
    )
    # **先占住业务键，再调外部**（P1）：原顺序是"先调钉钉建单、后落本地唯一键"，
    # 于是两件事都可能重复建单——并发请求、以及"钉钉建成功了但响应没回来"的重试。
    # 现在先落一行 submitting 并提交，把唯一键占实；并发对手会撞唯一约束后复用它。
    row.status = "submitting"
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        # 并发对手已占同一轮：复用它，**不再向钉钉要第二次实例**
        await session.rollback()
        existing = await get_by_business_key(
            session, inquiry_id=inquiry_id, inquiry_version=inquiry_version, oa_type=oa_type
        )
        if existing is not None:
            return existing
        raise

    try:
        instance_id = await get_client().create_process_instance(
            process_code=process_code,
            form_values=form_values,
            originator_user_id=originator_user_id,
        )
    except Exception as exc:  # 配置缺失/网络/钉钉业务错误：都落痕，不假装成功
        # 外部结果未知（超时/网络断）也归到这里：**标记 failed 并保留，不自动重试**
        # （业务确认的口径）。自动重试有重复建单的风险——宁可让人多点一下。
        row.status = "failed"
        row.error = f"{type(exc).__name__}: {exc}"[:500]
        await session.commit()
        return row

    row.instance_id = instance_id
    row.status = "pending"
    row.synced_at = datetime.now(UTC)
    await session.commit()
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
        raw_status = str(data.get("status") or "").upper()
        # 审批**走完**之后，通过还是驳回要看 `result`（agree / refuse）——
        # 只看 status 的话 COMPLETED 会被一律记成"已通过"，
        # **被驳回的单子在 CRM 里会显示成通过**，那是最危险的一类错。
        raw_result = str(data.get("result") or "").lower()
        if raw_status == "COMPLETED":
            if raw_result in ("refuse", "reject", "refused"):
                new_status = "rejected"
            elif raw_result in ("agree", "approved", "pass"):
                new_status = "approved"
            else:
                new_status = None
        else:
            new_status = _STATUS_MAP.get(raw_status)
        row.result = {"raw_status": raw_status, "raw_result": raw_result, "payload": data}
        row.synced_at = datetime.now(UTC)
        row.updated_at = datetime.now(UTC)
        if new_status and new_status != row.status:
            row.status = new_status
            changed += 1
    await session.flush()
    return {"checked": len(rows), "changed": changed}


async def first_image_attachment(
    session: AsyncSession, *, business_type: str, business_id: int
) -> tuple[str, bytes] | None:
    """取某个业务对象挂的第一张图片附件，返回 (文件名, 内容)。

    钉钉模板里「产品参考图片」是**必填的图片控件**，所以发起前必须有一张图。
    图不能直接塞进审批单，得先上传给钉钉换 media_id——**这一步也是对外的**，
    所以调用方必须在推送总闸打开时才真正传（关着的时候只记录"会用哪张图"）。
    """
    from app.modules.file.model import BusinessFile, FileRecord
    from app.modules.file.storage import absolute_path

    rows = (
        await session.execute(
            select(FileRecord)
            .join(BusinessFile, BusinessFile.file_id == FileRecord.id)
            .where(
                BusinessFile.business_type == business_type,
                BusinessFile.business_id == business_id,
            )
            .order_by(BusinessFile.id.asc())
        )
    ).scalars().all()
    for row in rows:
        mime = (row.mime_type or "").lower()
        if row.mime_type and not mime.startswith("image/"):
            continue
        path = absolute_path(row.object_key)
        if path.exists():
            return row.file_name, path.read_bytes()
    return None


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
