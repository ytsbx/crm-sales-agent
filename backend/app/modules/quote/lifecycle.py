"""正式对客动作：调用方先锁报价及版本，事实、留痕和通知待办同事务提交。"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog, write_audit
from app.core.errors import AppError, ErrorCode
from app.modules.followup.service import record_and_notify
from app.modules.notification.model import BusinessEvent
from app.modules.opportunity import service as opportunity_service
from app.modules.opportunity.model import Opportunity, OpportunityStage
from app.modules.quote.model import Quote, QuoteSendLog, QuoteVersion
from app.modules.quote.schema import SendRequest
from app.modules.quote.service import (
    ensure_freight_confirmed,
    ensure_items_master_confirmed,
    quote_is_expired,
)


def ensure_current_version(quote: Quote, version: QuoteVersion) -> None:
    if quote.current_version_id != version.id:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该版本已不是当前版本，请切换到当前版本操作", 422)


def ensure_customer_response_allowed(quote: Quote, version: QuoteVersion) -> None:
    ensure_current_version(quote, version)
    if (quote.status != "sent" or version.sent_at is None
            or version.approval_status != "approved"
            or version.accepted_at is not None or version.declined_at is not None):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "只有正式发送且未处理客户结果的当前版本才能接受或拒绝", 422)


async def _record(session, quote, version, operator_id, *, action, label, content, ip, after):
    await write_audit(session, operator_id=operator_id, action=action,
                      business_type="quote", business_id=quote.id,
                      after={"version_id": version.id, "version_no": version.version_no, **after}, ip=ip)
    await record_and_notify(
        session, customer_id=quote.customer_id, owner_id=quote.owner_id,
        operator_id=operator_id, exclude_user_id=operator_id,
        title=f"{label} {quote.quote_no} V{version.version_no}",
        content=content, business_type="quote", business_id=quote.id,
        quote_id=quote.id, opportunity_id=quote.opportunity_id,
        event_key=after.get("event_key") or f"quote:{action}:{version.id}",
    )


async def mark_version_sent(
    session: AsyncSession, *, quote: Quote, version: QuoteVersion,
    operator_id: int, payload: SendRequest, ip: str | None = None,
) -> bool:
    """第一次确认无 key 也安全重试；显式新 key 表示同版本再次实际发送。"""
    event_key = f"quote:send:{version.id}:{payload.request_key or 'initial'}"
    if (await session.execute(select(BusinessEvent.id).where(
            BusinessEvent.event_key == event_key))).scalar_one_or_none() is not None:
        previous = (await session.execute(select(AuditLog.after_data).where(
            AuditLog.business_type == "quote", AuditLog.business_id == quote.id,
            AuditLog.action == "send", AuditLog.after_data["event_key"].as_string() == event_key,
        ).limit(1))).scalar_one_or_none()
        if previous and (previous.get("channel") != payload.channel
                         or previous.get("receiver") != payload.receiver):
            raise AppError(ErrorCode.PARAM_ERROR, "该请求编号已用于另一条发送记录，请为实际再次发送使用新的编号", 422)
        return False
    # 兼容旧发送事实，不因接口重试补造一次发送或重置计时。
    if version.sent_at is not None and payload.request_key is None:
        return False
    ensure_current_version(quote, version)
    if version.approval_status != "approved":
        raise AppError(ErrorCode.APPROVAL_PENDING, "报价未通过审批，不能发送", 422)
    if (quote.status not in ("approved", "sent") or version.accepted_at is not None
            or version.declined_at is not None):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "当前报价状态不能标记发送，请新建版本后重新审批", 422)
    if quote_is_expired(quote.valid_until):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "报价已过有效期，请新建版本并更新有效期后发送", 422)
    # §8.14 的出口硬校验：印给客户的名称/规格/单位必须来自**已确认**的主数据。
    # 放在所有写操作之前 —— 拦下就不许写发送记录、不许推进商机。
    # 草稿阶段不受影响（生成明细时只提示），所以这里才是真正的闸门。
    await ensure_items_master_confirmed(session, version_id=version.id)
    # 运费闸门（2026-10-09 运费分离口径）：**草稿可以没填运费，正式发送必须已经
    # 确认具体金额**。与上面那条同一位置、同一性质 —— 拦下就不写发送记录、
    # 不推进商机。不加这道闸，客户文件上会印出没确认过的"运费 0.00"。
    await ensure_freight_confirmed(session, version_id=version.id)
    now = datetime.now(UTC)
    version.sent_at = now
    quote.status = "sent"
    log = QuoteSendLog(quote_version_id=version.id, channel=payload.channel,
                       receiver=payload.receiver, sent_by=operator_id, status="success", sent_at=now)
    session.add(log)
    await session.flush()
    content = (f"{quote.quote_no} V{version.version_no} 已正式发送，"
               f"渠道：{payload.channel}；收件人：{payload.receiver or '未登记'}")
    await _record(session, quote, version, operator_id, action="send", label="报价已正式发送",
                  content=content, ip=ip,
                  after={"channel": payload.channel, "receiver": payload.receiver,
                         "send_log_id": log.id, "event_key": event_key})
    await advance_quoted_stage(session, quote=quote, version=version,
                               operator_id=operator_id, ip=ip)
    return True


async def advance_quoted_stage(session: AsyncSession, *, quote: Quote, version: QuoteVersion,
                               operator_id: int, ip: str | None = None) -> bool:
    """本批唯一新增的自动阶段规则：正式发送推进已报价，不回退/重开商机。"""
    if not quote.opportunity_id:
        return False
    opportunity = (await session.execute(select(Opportunity).where(
        Opportunity.id == quote.opportunity_id
    ).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if (opportunity is None or opportunity.deleted_at is not None
            or opportunity.status != "open" or opportunity.customer_id != quote.customer_id):
        return False
    target = (await session.execute(select(OpportunityStage).where(
        OpportunityStage.code == "quoted", OpportunityStage.status == "active",
        OpportunityStage.is_win.is_(False), OpportunityStage.is_loss.is_(False),
    ))).scalar_one_or_none()
    current = await session.get(OpportunityStage, opportunity.stage_id)
    if target is None or current is None or current.is_win or current.is_loss:
        return False
    if current.id == target.id or current.sequence >= target.sequence:
        return False
    old_stage = current.name
    await opportunity_service.change_stage(
        session, opportunity, to_stage=target, operator_id=operator_id,
        remark=f"正式发送报价 {quote.quote_no} V{version.version_no}，自动推进",
    )
    await write_audit(session, operator_id=operator_id, action="change_stage",
                      business_type="opportunity", business_id=opportunity.id,
                      before={"stage": old_stage},
                      after={"stage": target.name, "source": "quote_sent",
                             "quote_id": quote.id, "quote_version_id": version.id}, ip=ip)
    return True


async def accept_version(
    session: AsyncSession, *, quote: Quote, version: QuoteVersion,
    operator_id: int, ip: str | None = None,
) -> bool:
    if version.accepted_at is not None:
        return False
    ensure_customer_response_allowed(quote, version)
    if quote_is_expired(quote.valid_until):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED,
                       f"报价已过有效期（{quote.valid_until}），请新建版本并更新有效期后接受", 422)
    version.accepted_at = datetime.now(UTC)
    quote.status = "accepted"
    await _record(session, quote, version, operator_id, action="accept", label="客户已接受报价",
                  content=f"{quote.quote_no} V{version.version_no} 客户已接受", ip=ip, after={})
    return True


async def decline_version(
    session: AsyncSession, *, quote: Quote, version: QuoteVersion,
    operator_id: int, reason: str | None, ip: str | None = None,
) -> bool:
    if version.declined_at is not None:
        return False
    ensure_customer_response_allowed(quote, version)
    version.declined_at = datetime.now(UTC)
    quote.status = "declined"
    await _record(session, quote, version, operator_id, action="decline", label="客户已拒绝报价",
                  content=f"{quote.quote_no} V{version.version_no} 客户已拒绝；原因：{reason or '未填写'}",
                  ip=ip, after={"reason": reason})
    return True
