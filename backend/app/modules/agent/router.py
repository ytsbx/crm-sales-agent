"""Sales Agent 接口（对齐 03-API §37）。"""

import asyncio
import json
from contextlib import aclosing
from datetime import UTC, datetime, timedelta

import anyio

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import idempotency
from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.agent import insights, runtime
from app.modules.agent.commentary import build_commentary
from app.modules.agent.model import (
    ACTION_STATUS_LABEL,
    RISK_LABEL,
    AgentAction,
    AgentExecution,
    AgentMessage,
    AgentSession,
)
from app.modules.agent.schema import (
    ActionReject,
    CustomerSummaryIn,
    FollowupSuggestionIn,
    MessageIn,
    OpportunityAnalysisIn,
    PricingAnalysisIn,
    ProductRecommendationIn,
    QuoteDraftIn,
    RiskAnalysisIn,
    SessionIn,
    StreamMessageIn,
)
from app.modules.agent.tools import TOOLS

router = APIRouter(tags=["Agent"])

#: 会话消息角色（03-API §37 Message）。tool 是 Agent 调用工具留下的过程记录，
#: 前端把它折叠起来展示，用户才能看清"它是怎么得出结论的"。
MESSAGE_ROLE_LABEL = {
    "user": "用户",
    "assistant": "助手",
    "tool": "工具调用",
}


async def _get_session(session: AsyncSession, session_id: int, user_id: int) -> AgentSession:
    return await runtime.get_active_session(session, session_id, user_id)


async def _get_action(
    session: AsyncSession, action_id: int, user_id: int, *, for_update: bool = False
) -> AgentAction:
    """取动作并校验归属。

    动作归属跟着会话走：不是自己的会话，连"这个 id 存在"都不该暴露，
    所以越权一律按 404 处理（与 _get_session 同口径）。
    """
    action = await session.get(AgentAction, action_id)
    if action is None:
        raise AppError(ErrorCode.NOT_FOUND, "动作不存在", 404)
    try:
        await runtime.get_active_session(session, action.session_id, user_id, for_update=for_update)
    except AppError as error:
        raise AppError(ErrorCode.NOT_FOUND, "动作不存在", 404) from error
    return action


@router.get("/agent/sessions")
async def list_sessions(
    before_id: int | None = Query(None, ge=1),
    limit: int = Query(30, ge=1, le=100),
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    """当前用户的会话，按 id 倒序；用 `before_id` 游标加载更早的会话。

    默认仍返回最近 30 条（老前端不受影响）。翻页用游标而不是 offset：
    新建会话会插到最前面，用 offset 翻页会把同一条会话挤到两页或漏掉。
    """
    stmt = (
        select(AgentSession)
        .where(AgentSession.user_id == user.id, AgentSession.deleted_at.is_(None))
        .order_by(AgentSession.id.desc())
        .limit(limit)
    )
    if before_id is not None:
        stmt = stmt.where(AgentSession.id < before_id)
    rows = (await session.execute(stmt)).scalars().all()
    return ok(
        [
            {
                "id": row.id,
                "title": row.title,
                "context_type": row.context_type,
                "context_id": row.context_id,
                "updated_at": row.updated_at,
            }
            for row in rows
        ]
    )


@router.post("/agent/sessions")
async def create_session(
    payload: SessionIn,
    request: Request,
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    row = AgentSession(
        user_id=user.id,
        title=payload.title or "新会话",
        context_type=payload.context_type,
        context_id=payload.context_id,
    )
    session.add(row)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        source="AGENT",
        action="create",
        business_type="agent_session",
        business_id=row.id,
        after={
            "context_type": row.context_type,
            "context_id": row.context_id,
        },
        ip=client_ip(request),
    )
    await session.commit()
    return ok({"id": row.id, "title": row.title}, "会话已创建")


@router.get("/agent/sessions/{session_id}")
async def get_session_detail(
    session_id: int,
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    row = await _get_session(session, session_id, user.id)
    # 长对话默认给**最新** 200 条可见消息（user/assistant，不含 tool），
    # 更早的由 `/messages/older` 按游标补。此前是升序 limit(200)，
    # 超过 200 条后最新消息反而打不开，且 tool 消息会占掉可见条数。
    message_rows = (
        await session.execute(
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id, AgentMessage.role != "tool")
            .order_by(AgentMessage.id.desc())
            .limit(201)
        )
    ).scalars().all()
    messages_has_more = len(message_rows) > 200
    messages = list(reversed(message_rows[:200]))
    actions = (
        await session.execute(
            select(AgentAction)
            .where(AgentAction.session_id == session_id)
            .order_by(AgentAction.id.asc())
        )
    ).scalars().all()
    return ok(
        {
            "session": {
                "id": row.id,
                "title": row.title,
                "context_type": row.context_type,
                "context_id": row.context_id,
            },
            "messages": [
                {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "tool_name": m.tool_name,
                    "created_at": m.created_at,
                }
                for m in messages
            ],
            "messages_has_more": messages_has_more,
            "actions": [
                {**runtime.serialize_action(a), "display": await runtime.action_display(session, a, user=user)}
                for a in actions
            ],
        }
    )


@router.delete("/agent/sessions/{session_id}")
async def delete_session(
    session_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    row = await runtime.get_active_session(session, session_id, user.id, for_update=True)
    await session.execute(delete(AgentMessage).where(AgentMessage.session_id == session_id))
    await session.execute(delete(AgentAction).where(
        AgentAction.session_id == session_id,
        AgentAction.status.in_(("proposed", *runtime.PENDING_ACTION_STATUSES, "expired")),
    ))
    # B16-05：会话删除连执行记录一起清，与消息同口径；下面查询再滤软删会话兜历史行
    await session.execute(delete(AgentExecution).where(AgentExecution.session_id == session_id))
    row.deleted_at = datetime.now(UTC)
    row.title = "已删除的会话"
    row.context_type = None
    row.context_id = None
    await write_audit(
        session,
        operator_id=user.id,
        source="AGENT",
        action="delete",
        business_type="agent_session",
        business_id=session_id,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "会话已删除")


@router.post("/agent/sessions/{session_id}/messages")
async def send_message(
    session_id: int,
    payload: MessageIn,
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    row = await _get_session(session, session_id, user.id)
    try:
        async with asyncio.timeout(90):
            result = await runtime.run_turn(
                session, agent_session=row, user=user, text=payload.content
            )
    except TimeoutError as exc:
        raise AppError(ErrorCode.SYSTEM_ERROR, runtime.model_error_message(exc), 504) from exc
    return ok(result)


@router.get("/agent/sessions/{session_id}/messages")
async def list_messages(
    session_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    """会话消息历史（03-API §37 Message）。

    含 user / assistant / tool 三种角色的消息 —— 前端要能把"Agent 调了哪个
    工具"展示成可折叠的过程，而不只是最终答复。
    """
    await _get_session(session, session_id, user.id)
    stmt = (
        select(AgentMessage)
        .where(AgentMessage.session_id == session_id)
        .order_by(AgentMessage.id.asc())
    )
    rows, total = await paginate(session, stmt, page, page_size)
    return ok(
        page_data(
            [
                {
                    "id": row.id,
                    "session_id": row.session_id,
                    "role": row.role,
                    "role_label": MESSAGE_ROLE_LABEL.get(row.role, row.role),
                    "content": row.content,
                    "tool_name": row.tool_name,
                    "created_at": row.created_at,
                }
                for row in rows
            ],
            total,
            page,
            page_size,
        )
    )


@router.get("/agent/sessions/{session_id}/messages/older")
async def list_older_messages(
    session_id: int,
    before_id: int = Query(..., ge=1),
    limit: int = Query(100, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    """按消息 id 游标向前翻，补会话详情里最新 200 条之外的更早消息。

    与详情同一口径：只回 user/assistant，tool 过程不占可见条数。
    多取一条判断 `has_more`，返回时仍按 id 升序，前端可以直接 prepend。
    """
    await _get_session(session, session_id, user.id)
    rows = (
        await session.execute(
            select(AgentMessage)
            .where(
                AgentMessage.session_id == session_id,
                AgentMessage.role != "tool",
                AgentMessage.id < before_id,
            )
            .order_by(AgentMessage.id.desc())
            .limit(limit + 1)
        )
    ).scalars().all()
    has_more = len(rows) > limit
    items = list(reversed(rows[:limit]))
    return ok(
        {
            "items": [
                {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "tool_name": m.tool_name,
                    "created_at": m.created_at,
                }
                for m in items
            ],
            "has_more": has_more,
        }
    )


@router.get("/agent/actions/{action_id}")
async def get_action(
    action_id: int,
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    """单个 Agent 动作详情（03-API §37 Action）。"""
    action = await _get_action(session, action_id, user.id)
    return ok(
        {
            **runtime.serialize_action(action),
            "risk_label": RISK_LABEL.get(action.risk_level, action.risk_level),
            "display": await runtime.action_display(session, action, user=user),
        }
    )


@router.post("/agent/actions/{action_id}/cancel")
async def cancel_action(
    action_id: int,
    payload: ActionReject,
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    """取消一个还没执行的 Agent 动作（03-API §37 Action）。

    与"拒绝"的区别：拒绝是**对内容的否定**（记录进审计，说明"我不同意这么干"），
    取消是**我自己撤回**（比如刚点了提案又不想了）。两者终态都是不再执行，
    但语义与审计口径不同，所以分开。

    只有未执行、未拒绝、未失败的动作能取消 —— 已经落地的动作不能"取消"，
    那是业务回滚，得走各自的业务接口。
    """
    # 归属校验不锁会话；与确认、拒绝共享的是下面的动作行锁。
    action = await _get_action(session, action_id, user.id)
    # 锁后重读，避免取消覆盖已执行结果。
    await session.refresh(action, with_for_update=True)
    before = runtime.require_action_not_expired(action)
    if before in ("executed", "rejected", "cancelled", "failed"):
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"动作已是「{ACTION_STATUS_LABEL.get(action.status, action.status)}」，不能取消",
        )
    action.status = "cancelled"
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="cancel",
        business_type="agent_action",
        business_id=action.id,
        before={"status": before},
        after={"status": "cancelled", "reason": payload.reason},
        ip=None,
    )
    await session.commit()
    return ok(runtime.serialize_action(action), "已取消")


@router.get("/agent/executions/{execution_id}")
async def get_execution(
    execution_id: int,
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    """单次工具调用详情（03-API §37 Execution）。

    带上 `role_snapshot` / `data_scope_snapshot`：事后要能回答
    "当时他是以什么身份调的、能看到什么"（03-API §38 的要求）。
    """
    row = await session.get(AgentExecution, execution_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "执行记录不存在", 404)
    agent_session = await session.get(AgentSession, row.session_id)
    if agent_session is None or agent_session.user_id != user.id:
        raise AppError(ErrorCode.DATA_SCOPE_DENIED, "该执行记录不在你的数据范围内", 403)
    if agent_session.deleted_at is not None:  # B16-05：已删会话视同不存在
        raise AppError(ErrorCode.NOT_FOUND, "执行记录不存在", 404)
    return ok(
        {
            "id": row.id,
            "session_id": row.session_id,
            "action_id": row.action_id,
            "tool_name": row.tool_name,
            "risk_level": row.risk_level,
            "risk_label": RISK_LABEL.get(row.risk_level, row.risk_level),
            "user_id": row.user_id,
            "role_snapshot": row.role_snapshot,
            "data_scope_snapshot": row.data_scope_snapshot,
            "input_payload": row.input_payload,
            "output_payload": row.output_payload,
            "status": row.status,
            "error_message": row.error_message,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
        }
    )


@router.post("/agent/executions/{execution_id}/retry")
async def retry_execution(
    execution_id: int,
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    """重试一次失败的工具调用（03-API §37 Execution）。

    只能重试**失败**的执行：成功的重试会造成重复写入（比如重复建跟进），
    那是业务事故而不是"重试"。

    重试的对象是工具本身，用原来的入参再跑一次，并记一条新的执行记录 ——
    不覆盖原记录，失败历史要留着（排查"为什么一直失败"要看它）。
    """
    row = await session.get(AgentExecution, execution_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "执行记录不存在", 404)
    agent_session = await session.get(AgentSession, row.session_id)
    if agent_session is None or agent_session.user_id != user.id:
        raise AppError(ErrorCode.DATA_SCOPE_DENIED, "该执行记录不在你的数据范围内", 403)
    if agent_session.deleted_at is not None:  # B16-05：已删会话不允许重试其执行
        raise AppError(ErrorCode.NOT_FOUND, "执行记录不存在", 404)
    if row.status != "failed":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "只有执行失败的工具调用可以重试；运行中或已完成的记录不能重试",
        )

    # ⚠️ 这里**不再**要求 `model_ready()`（2026-10-14 审查修）。
    # `runtime.retry_tool_call` 只做一件事：拿原来的入参再调一次 `spec.handler(ctx, ...)`
    # —— 全程**不碰模型**（不建客户端、不发 chat.completions）。而没配
    # `DEEPSEEK_API_KEY` 的部署本身是被支持的（此时 Agent 对话不可用，
    # 但工具与页面都在）。原来那道门让"重试"按钮在这种部署里**永久 422**，
    # 而它想防的"模型没配"与这次操作毫无关系。

    agent_session = await runtime.get_active_session(session, row.session_id, user.id, for_update=True)
    result = await runtime.retry_tool_call(
        session, execution_row=row, user=user, agent_session=agent_session
    )
    await session.commit()
    return ok(result, "已重试")


AGENT_TURN_ACTION = "agent:stream-turn"


@router.get("/agent/sessions/{session_id}/turns/{request_key}")
async def stream_turn_status(
    session_id: int,
    request_key: str,
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    await _get_session(session, session_id, user.id)
    key = idempotency.RequestKey
    row = (await session.execute(select(key).where(
        key.user_id == user.id, key.action == AGENT_TURN_ACTION,
        key.request_key == request_key, key.result_id == session_id,
    ))).scalar_one_or_none()
    if row is None:
        return ok({"status": "not_received"})
    if row.status == "done":
        return ok({"status": "done"})
    created_at = row.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    if row.status == "failed" or created_at <= datetime.now(UTC) - timedelta(minutes=2):
        return ok({"status": "interrupted"})
    return ok({"status": "running"})


@router.post("/agent/sessions/{session_id}/messages/stream")
async def stream_message(
    session_id: int,
    payload: StreamMessageIn,
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    """流式跑一轮对话（03-API §37 Message）——**token 级打字机**。

    `run_turn_events` 是异步生成器：模型每吐一段文本就产生一个 `delta`
    事件，这里逐事件转成 SSE 帧、立刻 flush，前端逐字渲染。
    工具调用（`tool_call`）、说明（`notice`）、最终结果（`done`）沿用了
    原事件流协议，前端兼容两种节奏：没有 delta 时等 done 一次性渲染。

    事件类型：`start` / `user_message` / `delta` / `tool_call` / `notice` / `error` / `done`。
    """
    row = await _get_session(session, session_id, user.id)

    reservation = None
    reservation_id = None
    if payload.request_key is not None:
        reservation = await idempotency.reserve(
            session, user_id=user.id, action=AGENT_TURN_ACTION, request_key=payload.request_key,
            payload={"session_id": session_id, "content": payload.content},
            result_type="agent_session",
        )
        if not reservation.should_replay:
            reservation_id = reservation.row.id
            reservation.row.result_id = session_id
            await session.commit()

    async def event_stream():
        if reservation is not None and reservation.should_replay:
            yield _sse("notice", {"reason": "turn_already_saved"})
            return
        finished = False
        try:
            yield _sse("start", {"session_id": session_id})
            async with asyncio.timeout(90):
                async with aclosing(runtime.run_turn_events(
                    session, agent_session=row, user=user, text=payload.content
                )) as events:
                    async for kind, data in events:
                        if kind == "done" and reservation is not None:
                            # 会话详情是回复和待确认操作的唯一来源。
                            await idempotency.complete(
                                session, reservation, result_payload={"status": "done"}, result_id=session_id,
                            )
                            await session.commit()
                            finished = True
                        yield _sse(kind, data)
        except Exception as exc:
            show_message = isinstance(exc, AppError) and exc.http_status < 500 and exc.code < 50000
            yield _sse("error", {"message": exc.message if show_message else runtime.model_error_message(exc)})
        finally:
            if reservation_id is not None and not finished:
                # 浏览器断开时先让运行时保留已有内容，再标识这次提交已中断。
                with anyio.move_on_after(5, shield=True):
                    await session.rollback()
                    key = idempotency.RequestKey
                    await session.execute(update(key).where(
                        key.id == reservation_id, key.status == "in_flight",
                    ).values(status="failed"))
                    await session.commit()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _sse(event: str, data: dict) -> str:
    """SSE 帧：`event:` + `data:`（JSON 单行，避免多行 data 被拆散）。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/agent/actions")
async def list_actions(
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    stmt = (
        select(AgentAction)
        .join(AgentSession, AgentSession.id == AgentAction.session_id)
        .where(AgentSession.user_id == user.id, AgentSession.deleted_at.is_(None))
    )
    cutoff = runtime.action_expiry_cutoff()
    if status == "expired":
        stmt = stmt.where(
            AgentAction.status.in_(runtime.PENDING_ACTION_STATUSES), AgentAction.created_at <= cutoff
        )
    elif status:
        stmt = stmt.where(AgentAction.status == status)
        if status in runtime.PENDING_ACTION_STATUSES:
            stmt = stmt.where(AgentAction.created_at > cutoff)
    rows, total = await paginate(session, stmt.order_by(AgentAction.id.desc()), page, page_size)
    items = [
        {
            **runtime.serialize_action(row),
            "display": await runtime.action_display(session, row, user=user),
        }
        for row in rows
    ]
    return ok(page_data(items, total, page, page_size))


@router.post("/agent/actions/{action_id}/confirm")
async def confirm_action(
    action_id: int,
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    action = await session.get(AgentAction, action_id)
    if action is None:
        raise AppError(ErrorCode.NOT_FOUND, "动作不存在", 404)
    # 归属校验不需要锁会话行：真正防并发双击的是 `execute_action` 里的动作行锁。
    # 锁会话会把"拒绝正在校验归属"与"确认"串行化，反而拖住确认。
    agent_session = await runtime.get_active_session(session, action.session_id, user.id)
    if agent_session.user_id != user.id:
        raise AppError(ErrorCode.FORBIDDEN, "只能确认自己的会话", 403)
    result = await runtime.execute_action(session, action=action, user=user)
    return ok({"result": result, "action": runtime.serialize_action(action)}, "已执行")


@router.post("/agent/actions/{action_id}/reject")
async def reject_action(
    action_id: int,
    payload: ActionReject,
    request: Request,
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    action = await session.get(AgentAction, action_id)
    if action is None:
        raise AppError(ErrorCode.NOT_FOUND, "动作不存在", 404)
    # 归属校验不锁会话；确认与拒绝共享的是下面这把**动作行锁**，
    # 等待后重读，避免用旧状态覆盖已完成动作。
    await runtime.get_active_session(session, action.session_id, user.id)
    await session.refresh(action, with_for_update=True)
    if runtime.require_action_not_expired(action) not in runtime.PENDING_ACTION_STATUSES:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该动作已经处理过了")
    action.status = "rejected"
    action.result = {"reason": payload.reason}
    action.confirmed_by = user.id
    action.confirmed_at = None
    await session.flush()
    # 拒绝也是决策，要和"确认"一样留痕
    await write_audit(
        session,
        operator_id=user.id,
        source="AGENT",
        action="reject_agent_action",
        business_type=action.business_type or "agent_action",
        business_id=action.business_id,
        after={
            "agent_action_id": action.id,
            "tool_name": action.tool_name,
            "reason": payload.reason,
        },
        ip=client_ip(request),
    )
    await session.commit()
    return ok(runtime.serialize_action(action), "已取消该动作")


@router.get("/agent/executions")
async def list_executions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    """我的工具调用记录（03-API §37 Execution）。

    按会话归属过滤：此前这里没过滤，任何有 agent:use 的人都能看到
    别人调了什么工具、传了什么参数（参数里可能带客户信息）。
    """
    stmt = (
        select(AgentExecution)
        .join(AgentSession, AgentSession.id == AgentExecution.session_id)
        .where(AgentSession.user_id == user.id)
        .where(AgentSession.deleted_at.is_(None))  # B16-05：已删会话的执行记录不再露出
        .order_by(AgentExecution.id.desc())
    )
    rows, total = await paginate(session, stmt, page, page_size)
    return ok(
        page_data(
            [
                {
                    "id": row.id,
                    "session_id": row.session_id,
                    "tool_name": row.tool_name,
                    "risk_level": row.risk_level,
                    "risk_label": RISK_LABEL.get(row.risk_level, row.risk_level),
                    "status": row.status,
                    "error_message": row.error_message,
                    "started_at": row.started_at,
                    "finished_at": row.finished_at,
                }
                for row in rows
            ],
            total,
            page,
            page_size,
        )
    )


@router.get("/agent/tools")
async def list_tools(
    _: CurrentUser = Depends(require_permission("agent:use")),
):
    """把工具清单暴露出来，便于前端展示"Agent 能做什么、哪些要确认"。"""
    return ok(
        [
            {
                "name": spec.name,
                "label": spec.label,
                "description": spec.description,
                "risk_level": spec.risk,
            }
            for spec in TOOLS.values()
        ]
    )


# ================================================================== 专用分析
# 03-API §37 Specialized。
#
# 这些接口的主体是**本地确定性分析**（逾期金额、停滞天数、建议价…），
# 永远可用、可测试；模型只负责多给一段人话叙述。
# 详见 insights.py 的模块说明。

def _ensure_analysis_permissions(user: CurrentUser, *permissions: str) -> None:
    if 'admin' not in user.roles and any(not user.has(code) for code in permissions):
        raise AppError(ErrorCode.FORBIDDEN, '没有相关业务模块的查看权限，无法使用这项 AI 分析', 403)


async def _with_commentary(topic: str, payload: dict) -> dict:
    """补上 `commentary` 字段：让模型把已经算好的事实讲成人话。

    返回给前端的永远是「统计结果 + 可选的叙述」：
    模型没配、超时、报错，都只是 `commentary` 为 `None` 且 `commentary_note`
    写清原因 —— 而不是让整个接口失败。否则"回款风险"这种纯统计能力
    就被一个外部服务的状态绑死了。

    `topic` 决定给模型的关注点（同一份数据，客户摘要和商机分析讲法不同）。
    """
    commentary, note = await build_commentary(topic, payload)
    return {**payload, "commentary": commentary, "commentary_note": note}


@router.post("/agent/customer-summary")
async def customer_summary(
    payload: CustomerSummaryIn,
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    """客户摘要（03-API §37）。"""
    _ensure_analysis_permissions(user, "customer:view")
    result = await insights.customer_summary(
        session, user, customer_id=payload.customer_id
    )
    return ok(await _with_commentary("customer-summary", result))


@router.post("/agent/opportunity-analysis")
async def opportunity_analysis(
    payload: OpportunityAnalysisIn,
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    """商机分析（03-API §37）：阶段停留时长、逾期任务、缺失信息。"""
    _ensure_analysis_permissions(user, "opportunity:view")
    result = await insights.opportunity_analysis(
        session, user, opportunity_id=payload.opportunity_id
    )
    return ok(await _with_commentary("opportunity-analysis", result))


@router.post("/agent/product-recommendation")
async def product_recommendation(
    payload: ProductRecommendationIn,
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    """产品推荐（03-API §37）：复用商机模块的推荐实现。"""
    _ensure_analysis_permissions(user, 'product:view')
    if payload.opportunity_id is not None:
        _ensure_analysis_permissions(user, 'opportunity:view')
    if payload.customer_id is not None:
        _ensure_analysis_permissions(user, 'customer:view')
    result = await insights.product_recommendation(
        session,
        user,
        customer_id=payload.customer_id,
        opportunity_id=payload.opportunity_id,
        limit=payload.limit,
    )
    return ok(await _with_commentary("product-recommendation", result))


@router.post("/agent/pricing-analysis")
async def pricing_analysis(
    payload: PricingAnalysisIn,
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    """核价分析（03-API §37）：把核价结果翻译成可执行的判断。"""
    _ensure_analysis_permissions(user, 'product:view')
    if payload.customer_id is not None:
        _ensure_analysis_permissions(user, 'customer:view')
    result = await insights.pricing_analysis(
        session,
        user,
        sku_id=payload.sku_id,
        quantity=payload.quantity,
        customer_id=payload.customer_id,
    )
    return ok(await _with_commentary("pricing-analysis", result))


@router.post("/agent/quote-draft")
async def quote_draft(
    payload: QuoteDraftIn,
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    """报价草稿建议（03-API §37）：**只给建议，不落库**。"""
    _ensure_analysis_permissions(user, 'opportunity:view', 'product:view')
    if payload.customer_id is not None:
        _ensure_analysis_permissions(user, 'customer:view')
    result = await insights.quote_draft(
        session,
        user,
        opportunity_id=payload.opportunity_id,
        customer_id=payload.customer_id,
    )
    return ok(await _with_commentary("quote-draft", result))


@router.post("/agent/followup-suggestion")
async def followup_suggestion(
    payload: FollowupSuggestionIn,
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    """跟进建议（03-API §37）：规则推出来的，每条都能指向具体事实。"""
    if payload.customer_id is not None:
        _ensure_analysis_permissions(user, 'customer:view')
    if payload.lead_id is not None:
        _ensure_analysis_permissions(user, 'lead:view')
    result = await insights.followup_suggestion(
        session, user, customer_id=payload.customer_id, lead_id=payload.lead_id
    )
    return ok(await _with_commentary("followup-suggestion", result))


@router.post("/agent/risk-analysis")
async def risk_analysis(
    payload: RiskAnalysisIn,
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    """回款风险分析（03-API §37）。

    纯统计就能给出"有 3 个节点逾期、合计 12 万、最长 45 天"，
    没配模型也照样能报警 —— 这正是它不该依赖大模型的原因。
    """
    _ensure_analysis_permissions(user, "payment:view")
    result = await insights.receivable_risk(session, user, order_id=payload.order_id)
    return ok(await _with_commentary("risk-analysis", result))
