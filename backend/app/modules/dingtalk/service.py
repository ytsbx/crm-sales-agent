"""询价审批的发起与结果回收（文档 §11.3 :152 / 场景11）。

链路：CRM 需求版本 → 发起钉钉审批实例 → 拿回实例 ID → 回收状态与结果 → 回到那条需求。

## 两条纪律

**1. 幂等靠业务键，不靠实例 ID。**
场景11 要求"不重复建 OA 单"。重复提交时我们**压根不该再向钉钉要一次实例**——
等实例 ID 回来再判重，单子已经建出去了。所以先用
（需求 + 需求版本 + OA 类型）查 `oa_instances`，命中就直接返回既有行；
真并发时还有唯一约束兜底（IntegrityError 里回查一次）。

**2. 表单值挂在 `formComponentValues` 上，且要按控件对齐。**
发起审批实例的请求体里，表单值是 `formComponentValues` 这个数组
（**不是 `formValues`**——那个字段不存在，我们写错过一次，后来拿官方 SDK 核对了）；
数组元素里 `name` / `value` 必填，另有可选的 `id` / `componentType`。
控件对不上时**钉钉不报错，只把那格留空**，于是"预填成功"的假象下业务还得手填一遍，
正好把"免重复录入"这条验收标准踩没。所以 `build_form_component_values`
只认调用方给的字段映射，映射从模板字段清单来（见 17-交接说明 §6 的待外部输入）。
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.dingtalk.client import DingTalkError, get_client
from app.modules.dingtalk.model import OA_STATUS_LABEL, OaInstance

#: 停在 `submitting` 超过这个时长，就认为那次尝试已经死了（进程被杀/机器重启），
#: 允许重新发起。取 10 分钟：正常的"占键 → 调钉钉"是秒级，
#: 留足余量给网络慢的情况，同时用户等十分钟后再点也确实该重试了。
SUBMITTING_STUCK_AFTER = timedelta(minutes=10)

#: 钉钉的审批状态词 → CRM 侧口径。CRM 侧固定这五个值，
#: 前端和查询都不用认对方系统的用词（与 ERP Adapter 同一套做法：翻译只发生在一处）。
_STATUS_MAP = {
    "RUNNING": "pending",
    "COMPLETED": "approved",
    "TERMINATED": "rejected",
    "CANCELED": "withdrawn",
}


def build_form_component_values(field_map: dict[str, Any]) -> list[dict[str, Any]]:
    """把 CRM 字段拼成钉钉的 `formComponentValues`（发起审批实例的请求体）。

    - `field_map` 的 key 必须是**模板控件的 id**（不是中文名）；
    - `name` 和 `id` 都填控件 id：新版接口以 `id` 认控件，而 `name` 按官方文档
      也允许填 id——两个都带上，任一判据都能命中；
    - `componentType` 从控件 id 的前缀推导（`TextField_XXX` → `TextField`）。
      **这不是猜的**：实测两张模板共 69 个控件，服务端返回的 `componentType`
      与 id 前缀**全部一致**；推导不出来的（id 里没有下划线）就不带这个字段；
    - 空值**不提交**——钉钉对空值控件会覆盖已有内容，把"没填"当成"清空"，
      这在"驳回后重提"时会把上一次填的内容抹掉。
    """
    items: list[dict[str, Any]] = []
    for component_id, value in field_map.items():
        if value in (None, "", [], {}):
            continue
        item: dict[str, Any] = {"name": component_id, "id": component_id, "value": value}
        if "_" in component_id:
            item["componentType"] = component_id.split("_", 1)[0]
        items.append(item)
    return items


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
    from app.core.config import settings as app_settings

    # 重试路径复用的是"已经存在的行"（见下面的同轮幂等判断），所以初值必须是 None，
    # 否则后面 `if row is not None` 会误判成复用、跳过首次插入
    row: OaInstance | None = None
    latest = await get_by_business_key(
        session, inquiry_id=inquiry_id, inquiry_version=inquiry_version, oa_type=oa_type
    )
    if latest is not None and not resubmit:
        # 同轮幂等**不能只看"有没有记录"，还要看那条记录是什么状态**，否则两种
        # 卡死都无解：
        # - `submitting`：进程在"占业务键的提交"与"调完外部之后的提交"之间被杀，
        #   行就会停在 submitting——轮询只捞 pending，同轮又原样返回，既不更新也不重发。
        #
        #   **口径（2026-10-04 定）：结果不明 → 转人工，不自动重发。**
        #   钉钉 workflow 接口没有幂等键，本地 idempotency_key 只保证"本地一行"。
        #   如果上一次请求其实已经到达钉钉、只是响应丢了，自动重发就会在钉钉建出
        #   第二张审批单，本地 instance_id 被第二张覆盖、首张成"隐形挂单"。所以超过
        #   阈值后把行标成 needs_review，由人工去钉钉核对后再决定：认领 / 重发 / 作废。
        # - `skipped`：当时总闸关着没发。闸门开了之后再点"发起"，
        #   如果还返回那条 skipped，用户点了等于没点。
        if latest.status == "submitting":
            # 用 **last_attempt_at**（本次尝试的起点）而不是 created_at：
            # created_at 是"这条记录什么时候产生的"（第一次发起）。
            # 存量行由迁移抄过一份，这里再兜一层 created_at（老数据/异常数据）。
            started = latest.last_attempt_at or latest.created_at or datetime.now(UTC)
            if (datetime.now(UTC) - started) > SUBMITTING_STUCK_AFTER:
                latest.status = "needs_review"
                latest.error = (
                    "上次发起结果不明（发起过程中断）：钉钉那边可能已经建了审批单。"
                    "已转人工核对——请先去钉钉确认，再选择「认领 / 重发 / 作废」"
                )
                await session.commit()
            return latest
        if latest.status == "skipped" and not app_settings.dingtalk_push_off:
            # 闸门开了，重走一遍（复用同一行、同一幂等键）。skipped 是"根本没发"，
            # 没有重复建单风险，这一条保留自动。
            latest.status = "submitting"
            latest.error = None
            latest.last_attempt_at = datetime.now(UTC)  # 本次尝试的新起点
            row = latest
            await session.commit()
        else:
            return latest

    submit_round = (int(latest.submit_round) + 1) if (resubmit and latest) else 1
    idempotency_key = f"{inquiry_id}:{inquiry_version}:{oa_type}:{submit_round}"

    component_values = build_form_component_values(field_map)

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
            form_snapshot={"formComponentValues": component_values},
            status="skipped",
            error="钉钉推送已关闭（DINGTALK_PUSH_OFF），未向钉钉发起审批",
            created_by=user.id,
            created_at=datetime.now(UTC),
            last_attempt_at=datetime.now(UTC),
        )
        session.add(blocked)
        await session.flush()
        return blocked

    if row is not None:
        # 重试路径：刷新报文快照，保证"本次实际发出去的"与记录一致；
        # 复用同一行，所以不需要再走一次"占业务键"的插入与唯一约束处理
        row.form_snapshot = {"formComponentValues": component_values}
        await session.flush()
    else:
        row = OaInstance(
            customer_id=customer_id,
            inquiry_id=inquiry_id,
            inquiry_version=inquiry_version,
            oa_type=oa_type,
            idempotency_key=idempotency_key,
            submit_round=submit_round,
            process_code=process_code,
            originator_user_id=originator_user_id,
            form_snapshot={"formComponentValues": component_values},
            status="pending",
            created_by=user.id,
            created_at=datetime.now(UTC),
            last_attempt_at=datetime.now(UTC),
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
                session,
                inquiry_id=inquiry_id,
                inquiry_version=inquiry_version,
                oa_type=oa_type,
            )
            if existing is not None:
                return existing
            raise

    try:
        instance_id = await get_client().create_process_instance(
            process_code=process_code,
            form_component_values=component_values,
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


async def resolve_reviewed_instance(
    session: AsyncSession,
    row: OaInstance,
    *,
    action: str,
    instance_id: str | None = None,
    note: str | None = None,
) -> OaInstance:
    """人工处理"结果不明"的发起（口径 2026-10-04：不自动重发，转人工）。

    三种决定，都要人先到钉钉那边看一眼再选：
    - `adopt`：钉钉其实已经建单 → 填实例号接过来，状态回"审批中"；
    - `resend`：确认钉钉没有单 → 复用同一轮重新发起（同一行、同一幂等键）；
    - `abandon`：确认不发了 → 作废本轮记录。
    """
    if action == "adopt":
        if not instance_id:
            raise AppError(
                ErrorCode.REQUIRED_FIELD_MISSING, "认领需要填写钉钉那边已有的审批单号", 422
            )
        row.instance_id = instance_id
        row.status = "pending"
        row.error = None
        await session.flush()
        return row
    if action == "resend":
        return await resend_instance(session, row)
    if action == "abandon":
        row.status = "withdrawn"
        row.error = note or "人工核对确认钉钉未建单，作废本轮"
        await session.flush()
        return row
    raise AppError(
        ErrorCode.PARAM_ERROR, "action 只能是 adopt / resend / abandon", 422
    )


async def resend_instance(session: AsyncSession, row: OaInstance) -> OaInstance:
    """人工确认钉钉没建单后，复用同一轮重新发起（不换行、不加轮次）。

    提交前先落 `submitting` 并提交：万一进程在调用钉钉前后又被打断，下一个人
    打开还能看到"结果不明"，不会被当成没发过而重复点。
    """
    component_values = (row.form_snapshot or {}).get("formComponentValues") or []
    row.status = "submitting"
    row.error = None
    row.last_attempt_at = datetime.now(UTC)
    await session.commit()
    try:
        instance_id = await get_client().create_process_instance(
            process_code=row.process_code or "",
            form_component_values=component_values,
            originator_user_id=row.originator_user_id or "",
        )
    except Exception as exc:
        row.status = "failed"
        row.error = f"{type(exc).__name__}: {exc}"[:500]
        await session.flush()
        return row
    row.instance_id = instance_id
    row.status = "pending"
    row.error = None
    row.synced_at = datetime.now(UTC)
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
