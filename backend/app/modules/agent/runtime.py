"""Agent 运行时：DeepSeek 对话 + 工具调用 + 风险网关。"""

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.config import settings
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.agent.model import (
    RISK_LABEL,
    AgentAction,
    AgentExecution,
    AgentMessage,
    AgentSession,
)
from app.modules.agent.tools import TOOLS, ToolContext, openai_tools, tool_label

SYSTEM_PROMPT = """你是公司销售 CRM 里的销售助手，服务对象是业务员和销售主管。

你的职责：
1. 回答关于客户、商机、报价、订单、回款的问题——**必须先用工具查数据，不要凭空猜测**；
2. 做核价：先 list_sku_options 拿到 sku_id，再 calculate_price，并把建议价、最低允许价、是否需要审批讲清楚；
3. 给出下一步动作建议，必要时提议记录跟进或创建任务。

纪律：
- 数字必须来自工具返回，不许编造客户名、金额、价格；
- 不要臆测权限：低于最低允许价的报价一律走 request_quote_approval，不要承诺可以特批；
- 用户要求记录跟进、创建任务、改商机下一步动作这类写操作时，
  **直接调用对应工具**（系统会自动把动作挂起、生成确认卡片），
  不要只在文字里问"要不要我帮你做"；
- 工具返回"待用户确认"后，用一两句话说明你准备了什么、等他点确认，不要说已经完成；
- 向用户说明你做了什么时用中文描述（例如"我查了客户档案""我算了一下成本"），
  **不要把工具函数名（如 search_customers）直接抛给用户**；
- 回答用中文，简洁、直接、可执行，不要说套话。

当前用户：{name}（角色：{roles}，数据范围：{scope}）
"""


def model_ready() -> bool:
    return bool(settings.deepseek_api_key)


def _client():
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        timeout=60,
    )


async def _history(session: AsyncSession, session_id: int, limit: int = 16) -> list[dict]:
    rows = (
        await session.execute(
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id, AgentMessage.role.in_(["user", "assistant"]))
            .order_by(AgentMessage.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    messages = []
    for row in reversed(rows):
        if row.content:
            messages.append({"role": row.role, "content": row.content})
    return messages


async def _record_execution(
    session: AsyncSession,
    *,
    session_id: int,
    action_id: int | None,
    tool_name: str,
    risk: str,
    payload: dict,
    output: dict | None,
    status: str,
    error: str | None = None,
    started_at: datetime | None = None,
) -> None:
    session.add(
        AgentExecution(
            session_id=session_id,
            action_id=action_id,
            tool_name=tool_name,
            risk_level=risk,
            input_payload=payload,
            output_payload=output,
            status=status,
            error_message=error,
            started_at=started_at or datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
    )


def _risk_summary(spec, args: dict) -> str:
    """把动作参数压成一句人话，给确认卡片用。"""
    if spec.name == "create_followup":
        return f"记录跟进：{args.get('content', '')[:40]}"
    if spec.name == "create_task":
        return f"创建任务：{args.get('title', '')}"
    if spec.name == "update_opportunity_next_action":
        return f"更新商机 {args.get('opportunity_id')} 的下一步动作：{args.get('next_action', '')}"
    if spec.name == "request_quote_approval":
        return f"把报价版本 {args.get('quote_version_id')} 提交审批"
    return f"执行 {spec.name}"


async def run_turn(
    session: AsyncSession,
    *,
    agent_session: AgentSession,
    user: CurrentUser,
    text: str,
) -> dict:
    """跑一轮对话：模型可能连续调用多个工具，写动作则挂起等确认。"""
    user_message = AgentMessage(session_id=agent_session.id, role="user", content=text)
    session.add(user_message)
    await session.commit()

    if not model_ready():
        reply = (
            "Agent 还没有配置模型。请在 backend/.env 里填好 DEEPSEEK_API_KEY 后重试"
            "（会话、工具与风控机制已经就绪，只差模型这一环）。"
        )
        session.add(
            AgentMessage(session_id=agent_session.id, role="assistant", content=reply)
        )
        await session.commit()
        return {"reply": reply, "actions": [], "tool_calls": []}

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT.format(
                name=user.name, roles="、".join(user.roles) or "未分配", scope=user.data_scope
            ),
        },
        *await _history(session, agent_session.id, limit=16),
    ]
    # _history 已经把本轮用户消息包含进去了，去重
    if messages[-1].get("content") == text and messages[-1].get("role") == "user":
        messages = messages[:-1] + [{"role": "user", "content": text}]

    tool_trace: list[dict] = []
    pending_actions: list[AgentAction] = []
    ctx = ToolContext(session=session, user=user, agent_session_id=agent_session.id)
    client = _client()
    reply_text = ""

    for _ in range(max(1, settings.agent_max_tool_rounds)):
        response = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=messages,
            tools=openai_tools(),
            tool_choice="auto",
            temperature=0.2,
        )
        message = response.choices[0].message
        calls = message.tool_calls or []
        if not calls:
            reply_text = message.content or ""
            break

        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                    for call in calls
                ],
            }
        )

        for call in calls:
            name = call.function.name
            spec = TOOLS.get(name)
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            if spec is None:
                result: dict = {"error": f"未知工具 {name}"}
            elif spec.risk == "L1":
                started = datetime.now(UTC)
                try:
                    result = await spec.handler(ctx, **args)
                    await _record_execution(
                        session,
                        session_id=agent_session.id,
                        action_id=None,
                        tool_name=name,
                        risk="L1",
                        payload=args,
                        output=result,
                        status="success",
                        started_at=started,
                    )
                    session.add(
                        AgentMessage(
                            session_id=agent_session.id,
                            role="tool",
                            tool_name=name,
                            content=json.dumps(result, ensure_ascii=False)[:2000],
                        )
                    )
                except AppError as exc:
                    result = {"error": exc.message}
                    await _record_execution(
                        session,
                        session_id=agent_session.id,
                        action_id=None,
                        tool_name=name,
                        risk="L1",
                        payload=args,
                        output=None,
                        status="failed",
                        error=exc.message,
                        started_at=started,
                    )
                tool_trace.append(
                    {
                        "tool": name,
                        "label": tool_label(name),
                        "risk": "L1",
                        "input": args,
                        "output": result,
                    }
                )
            else:
                # L2 / L3：不执行，落成待确认动作
                action = AgentAction(
                    session_id=agent_session.id,
                    action_type=name,
                    tool_name=name,
                    risk_level=spec.risk,
                    business_type=spec.business_type,
                    business_id=args.get("customer_id")
                    or args.get("opportunity_id")
                    or args.get("quote_version_id"),
                    title=_risk_summary(spec, args),
                    proposed_payload=args,
                    status="awaiting_confirmation" if spec.risk == "L2" else "approval_required",
                )
                session.add(action)
                await session.flush()
                pending_actions.append(action)
                result = {
                    "status": "awaiting_user_confirmation",
                    "action_id": action.id,
                    "risk_level": spec.risk,
                    "risk_label": RISK_LABEL[spec.risk],
                    "note": "该动作尚未执行，需要用户在界面上确认后才会生效",
                }
                tool_trace.append(
                    {
                        "tool": name,
                        "label": tool_label(name),
                        "risk": spec.risk,
                        "input": args,
                        "output": result,
                    }
                )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False)[:3000],
                }
            )
        await session.commit()

    if not reply_text:
        # 工具轮次用完了还没轮到模型说话：再要一句收尾总结（这次不给工具，逼它开口）
        try:
            final = await client.chat.completions.create(
                model=settings.deepseek_model,
                messages=messages
                + [
                    {
                        "role": "user",
                        "content": "请用两三句话总结上面的查询结果，并说明你准备了什么动作、需要我确认什么。",
                    }
                ],
                temperature=0.2,
            )
            reply_text = final.choices[0].message.content or ""
        except Exception:  # 模型抖动不该让整轮对话失败
            reply_text = ""
        if not reply_text:
            reply_text = "我已经查完了，结果在上面的工具记录里。"

    session.add(
        AgentMessage(session_id=agent_session.id, role="assistant", content=reply_text)
    )
    if agent_session.title in ("", "新会话"):
        agent_session.title = text[:20]
    await session.commit()
    actions_payload = [
        {**serialize_action(action), "display": await action_display(session, action)}
        for action in pending_actions
    ]
    return {
        "reply": reply_text,
        "actions": actions_payload,
        "tool_calls": tool_trace,
    }


def serialize_action(action: AgentAction) -> dict:
    return {
        "id": action.id,
        "tool_name": action.tool_name,
        "tool_label": tool_label(action.tool_name),
        "action_type": action.action_type,
        "risk_level": action.risk_level,
        "risk_label": RISK_LABEL.get(action.risk_level, action.risk_level),
        "title": action.title,
        "payload": action.proposed_payload,
        "status": action.status,
        "result": action.result,
        "created_at": action.created_at,
        "confirmed_at": action.confirmed_at,
    }


async def action_display(session: AsyncSession, action: AgentAction) -> dict:
    """把动作参数和结果翻译成人话。

    设计取舍：前端不该认识 quote_version_id 这类内部字段，
    更不该把原始 JSON 摊给业务员看——那是调试信息。
    所以由后端负责翻译，前端只渲染 label/value 列表。
    """
    from app.modules.customer.model import Customer
    from app.modules.opportunity.model import Opportunity
    from app.modules.quote.model import Quote, QuoteVersion
    from app.modules.task.model import Task

    payload = action.proposed_payload or {}
    fields: list[dict] = []

    async def customer_name(customer_id) -> str | None:
        if not customer_id:
            return None
        customer = await session.get(Customer, int(customer_id))
        return customer.name if customer else None

    async def opportunity_title(opportunity_id) -> str | None:
        if not opportunity_id:
            return None
        opportunity = await session.get(Opportunity, int(opportunity_id))
        return opportunity.title if opportunity else None

    if action.tool_name == "create_followup":
        name = await customer_name(payload.get("customer_id"))
        title = await opportunity_title(payload.get("opportunity_id"))
        if name:
            fields.append({"label": "客户", "value": name})
        if title:
            fields.append({"label": "商机", "value": title})
        fields.append({"label": "跟进方式", "value": payload.get("followup_type") or "未指定"})
        fields.append({"label": "跟进内容", "value": payload.get("content") or "-"})
        if payload.get("customer_feedback"):
            fields.append({"label": "客户反馈", "value": payload["customer_feedback"]})
        if payload.get("next_action"):
            fields.append({"label": "下一步", "value": payload["next_action"]})

    elif action.tool_name == "create_task":
        fields.append({"label": "任务", "value": payload.get("title") or "-"})
        due = payload.get("due_at")
        if due:
            fields.append({"label": "截止时间", "value": str(due)[:16].replace("T", " ")})
        fields.append(
            {
                "label": "优先级",
                "value": {"high": "高", "normal": "中", "low": "低"}.get(
                    str(payload.get("priority")), "中"
                ),
            }
        )
        name = await customer_name(payload.get("customer_id"))
        title = await opportunity_title(payload.get("opportunity_id"))
        if name:
            fields.append({"label": "关联客户", "value": name})
        if title:
            fields.append({"label": "关联商机", "value": title})

    elif action.tool_name == "update_opportunity_next_action":
        title = await opportunity_title(payload.get("opportunity_id"))
        if title:
            fields.append({"label": "商机", "value": title})
        fields.append({"label": "新的下一步动作", "value": payload.get("next_action") or "-"})

    elif action.tool_name == "request_quote_approval":
        version = await session.get(QuoteVersion, int(payload.get("quote_version_id") or 0))
        if version:
            quote = await session.get(Quote, version.quote_id)
            fields.append(
                {
                    "label": "报价单",
                    "value": f"{quote.quote_no} 第 {version.version_no} 版" if quote else f"版本 {version.id}",
                }
            )
            fields.append({"label": "报价金额", "value": f"¥{float(version.total_amount):,.2f}"})
        fields.append({"label": "申请原因", "value": payload.get("reason") or "未填写"})

    else:
        # 兜底：未知工具只列出参数键值，但仍不放原始 JSON
        for key, value in payload.items():
            fields.append({"label": key, "value": str(value)})

    result_text = ""
    result = action.result or {}
    if action.status == "executed":
        if action.tool_name == "create_followup":
            result_text = "跟进已记录"
        elif action.tool_name == "create_task":
            task = await session.get(Task, int(result.get("task_id") or 0))
            result_text = f"任务已创建：{task.title}" if task else "任务已创建"
        elif action.tool_name == "update_opportunity_next_action":
            result_text = "商机的下一步动作已更新"
        elif action.tool_name == "request_quote_approval":
            if result.get("approval_required"):
                result_text = "已提交审批，等待主管处理"
            else:
                result_text = "价格在权限内，报价已自动通过"
        else:
            result_text = "已执行"
    elif action.status == "rejected":
        result_text = "你取消了这次操作"
    elif action.status == "failed":
        result_text = f"执行失败：{result.get('error', '未知原因')}"
    elif action.status == "approval_required":
        result_text = "需要你确认后才会提交审批"
    elif action.status == "awaiting_confirmation":
        result_text = "等待你确认"

    return {
        "fields": fields,
        "result_text": result_text,
        "can_confirm": action.status in ("awaiting_confirmation", "approval_required"),
    }


async def execute_action(
    session: AsyncSession, *, action: AgentAction, user: CurrentUser
) -> dict:
    """用户确认后真正执行 L2 动作（L3 直接转审批流程）。"""
    if action.status not in ("awaiting_confirmation", "approval_required"):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该动作已经处理过了")
    spec = TOOLS.get(action.tool_name)
    if spec is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到对应的工具", 404)

    ctx = ToolContext(session=session, user=user, agent_session_id=action.session_id)
    started = datetime.now(UTC)
    try:
        result = await spec.handler(ctx, **(action.proposed_payload or {}))
    except AppError as exc:
        action.status = "failed"
        action.result = {"error": exc.message}
        await _record_execution(
            session,
            session_id=action.session_id,
            action_id=action.id,
            tool_name=action.tool_name,
            risk=action.risk_level,
            payload=action.proposed_payload or {},
            output=None,
            status="failed",
            error=exc.message,
            started_at=started,
        )
        await session.commit()
        raise

    action.status = "executed"
    action.result = result
    action.confirmed_by = user.id
    action.confirmed_at = datetime.now(UTC)
    await _record_execution(
        session,
        session_id=action.session_id,
        action_id=action.id,
        tool_name=action.tool_name,
        risk=action.risk_level,
        payload=action.proposed_payload or {},
        output=result,
        status="success",
        started_at=started,
    )
    session.add(
        AgentMessage(
            session_id=action.session_id,
            role="assistant",
            content=f"已执行：{action.title}",
        )
    )
    await session.commit()
    return result
