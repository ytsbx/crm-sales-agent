"""Sales Agent 接口（对齐 03-API §37）。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.agent import runtime
from app.modules.agent.model import (
    ACTION_STATUS_LABEL,
    AgentAction,
    AgentExecution,
    AgentMessage,
    AgentSession,
)
from app.modules.agent.schema import ActionReject, MessageIn, SessionIn
from app.modules.agent.tools import TOOLS

router = APIRouter(tags=["Agent"])


async def _get_session(session: AsyncSession, session_id: int, user_id: int) -> AgentSession:
    row = await session.get(AgentSession, session_id)
    if row is None or row.user_id != user_id:
        raise AppError(ErrorCode.NOT_FOUND, "会话不存在", 404)
    return row


@router.get("/agent/sessions")
async def list_sessions(
    user: CurrentUser = Depends(require_permission("agent:use")),
    session: AsyncSession = Depends(get_db),
):
    rows = (
        await session.execute(
            select(AgentSession)
            .where(AgentSession.user_id == user.id)
            .order_by(AgentSession.id.desc())
            .limit(30)
        )
    ).scalars().all()
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
            "title": row.title,
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
    messages = (
        await session.execute(
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id)
            .order_by(AgentMessage.id.asc())
            .limit(200)
        )
    ).scalars().all()
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
                if m.role != "tool"
            ],
            "actions": [
                {**runtime.serialize_action(a), "display": await runtime.action_display(session, a)}
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
    row = await _get_session(session, session_id, user.id)
    title = row.title
    await session.delete(row)
    await write_audit(
        session,
        operator_id=user.id,
        source="AGENT",
        action="delete",
        business_type="agent_session",
        business_id=session_id,
        before={"title": title},
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
    result = await runtime.run_turn(
        session, agent_session=row, user=user, text=payload.content
    )
    return ok(result)


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
        .where(AgentSession.user_id == user.id)
    )
    if status:
        stmt = stmt.where(AgentAction.status == status)
    rows, total = await paginate(session, stmt.order_by(AgentAction.id.desc()), page, page_size)
    items = [
        {
            **runtime.serialize_action(row),
            "status_label": ACTION_STATUS_LABEL.get(row.status, row.status),
            "display": await runtime.action_display(session, row),
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
    agent_session = await _get_session(session, action.session_id, user.id)
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
    await _get_session(session, action.session_id, user.id)
    if action.status not in ("awaiting_confirmation", "approval_required"):
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
    stmt = select(AgentExecution).order_by(AgentExecution.id.desc())
    rows, total = await paginate(session, stmt, page, page_size)
    return ok(
        page_data(
            [
                {
                    "id": row.id,
                    "tool_name": row.tool_name,
                    "risk_level": row.risk_level,
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
