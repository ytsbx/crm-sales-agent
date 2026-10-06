"""Agent 运行时：DeepSeek 对话 + 工具调用 + 风险网关。"""

import json
from collections.abc import Callable
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
from app.modules.agent.tools import (
    TOOLS,
    ToolContext,
    ensure_tool_permission,
    missing_permissions,
    openai_tools,
    tool_label,
)

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
- 记录跟进时，普通跟进须有下一动作和含时区的下次跟进时间；客户明确拒绝、业务关闭或等待外部固定节点可选免填原因。信息缺失先向用户补问，不猜日期或原因。
- 工具返回"待用户确认"后，用一两句话说明你准备了什么、等他点确认，不要说已经完成；
- 向用户说明你做了什么时用中文描述（例如"我查了客户档案""我算了一下成本"），
  **不要把工具函数名（如 search_customers）直接抛给用户**；
- 回答用中文，简洁、直接、可执行，不要说套话。

当前用户：{name}（角色：{roles}，数据范围：{scope}）{context}
"""


CONTEXT_LABEL = {
    "customer": "客户",
    "opportunity": "商机",
    "quote": "报价单",
    "order": "销售订单",
    "lead": "线索",
    "sample": "样品申请",
    "task": "任务",
}


def render_context(agent_session: AgentSession) -> str:
    """把会话携带的业务上下文写进系统提示词（详情页右侧 Copilot 用）。

    `agent_sessions.context_type / context_id` 这两列一直存在却没被用过，
    导致从报价详情页打开助手时，模型不知道用户正看着哪张报价单。
    """
    if not agent_session.context_type or not agent_session.context_id:
        return ""
    label = CONTEXT_LABEL.get(agent_session.context_type, agent_session.context_type)
    return (
        f"\n用户当前正在查看：{label} id={agent_session.context_id}。"
        "相关提问默认指这条记录，先用工具查它再回答。"
    )


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
    user: CurrentUser | None = None,
) -> None:
    """记录一次工具调用。

    `user` 用来落「谁、什么角色、什么数据范围」的快照（03-API §38 末段）。
    存快照而不是只存 user_id：角色和数据范围会变，事后要能还原
    "当时这个人有没有权限看到这条数据"。
    """
    session.add(
        AgentExecution(
            session_id=session_id,
            action_id=action_id,
            tool_name=tool_name,
            risk_level=risk,
            user_id=user.id if user else None,
            role_snapshot="、".join(user.roles) if user and user.roles else None,
            data_scope_snapshot=user.data_scope if user else None,
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


async def run_turn_events(
    session: AsyncSession,
    *,
    agent_session: AgentSession,
    user: CurrentUser,
    text: str,
):
    """跑一轮对话，逐事件产出（异步生成器）：模型可能连续调用多个工具，写动作则挂起等确认。

    事件 `(kind, data)`：
      user_message  用户消息已落库；
      delta         模型回复的**文本增量**——打字机效果的来源，来了就推，不缓存整轮；
      tool_call     一次工具执行完成（L1 出结果，L2/L3 落成待确认动作）；
      notice        需要说明的情况（模型未配置等）；
      done          整轮结束，data 是最终结果（reply / actions / tool_calls）。

    此前 run_turn 是"回调 + 返回值"的整块函数，流式端点只能把一轮跑完再
    把事件一次性吐出去。现在改成生成器：文本增量在模型吐出来的那一刻就让出，
    前端才能逐字渲染。让出点都是安全点——delta 让出时除已提交的用户消息外
    没有任何未提交的写状态；tool_call 让出时该工具已处理完毕，
    "写动作挂起等确认"的语义不受影响。
    """
    user_message = AgentMessage(session_id=agent_session.id, role="user", content=text)
    session.add(user_message)
    await session.commit()
    yield ("user_message", {"content": text})

    if not model_ready():
        reply = (
            "Agent 还没有配置模型。请在 backend/.env 里填好 DEEPSEEK_API_KEY 后重试"
            "（会话、工具与风控机制已经就绪，只差模型这一环）。"
        )
        session.add(
            AgentMessage(session_id=agent_session.id, role="assistant", content=reply)
        )
        await session.commit()
        yield ("notice", {"reason": "model_not_configured", "reply": reply})
        yield ("done", {"reply": reply, "actions": [], "tool_calls": []})
        return

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT.format(
                name=user.name,
                roles="、".join(user.roles) or "未分配",
                scope=user.data_scope,
                context=render_context(agent_session),
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

    async def chat_round(msgs: list[dict], with_tools: bool):
        """流式跑一轮模型调用。

        逐段 `yield ("delta", 增量文本)`；本轮收尾时 `yield ("round", (整段文本, 工具调用))`。
        工具调用按 OpenAI 流式规范累积：function.name 整段到达（后到覆盖），
        arguments 分片到达（逐片拼接）。
        """
        round_content: list[str] = []
        acc: dict[int, dict] = {}
        kwargs: dict = {
            "model": settings.deepseek_model,
            "messages": msgs,
            "temperature": 0.2,
            "stream": True,
        }
        if with_tools:
            kwargs["tools"] = openai_tools()
            kwargs["tool_choice"] = "auto"
        stream = await client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            piece = getattr(delta, "content", None)
            if piece:
                round_content.append(piece)
                yield ("delta", piece)
            for tc in delta.tool_calls or []:
                idx = tc.index if tc.index is not None else 0
                slot = acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn is None:
                    continue
                if fn.name:
                    slot["name"] = fn.name
                if fn.arguments:
                    slot["arguments"] += fn.arguments
        calls = [acc[i] for i in sorted(acc)]
        yield ("round", ("".join(round_content), calls))

    # 断线兜底用：累计所有已流出的文本增量。断开（刷新/断网/关页面）会把
    # 生成器关闭（GeneratorExit），此时把已生成的部分回复落库，
    # 否则会话历史里"AI 说到一半的话"整段消失、刷新后对不上。
    streamed_parts: list[str] = []
    _reply_saved = False

    try:
        for _ in range(max(1, settings.agent_max_tool_rounds)):
            content, calls = "", []
            async for ev_kind, payload in chat_round(messages, with_tools=True):
                if ev_kind == "delta":
                    streamed_parts.append(payload)
                    yield ("delta", {"text": payload})
                else:
                    content, calls = payload
            if not calls:
                reply_text = content
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": content or "",
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": call["arguments"],
                            },
                        }
                        for call in calls
                    ],
                }
            )

            for call in calls:
                name = call["name"]
                spec = TOOLS.get(name)
                try:
                    args = json.loads(call["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}

                if spec is None:
                    result: dict = {"error": f"未知工具 {name}"}
                elif missing_permissions(spec, user):
                    # 执行网关的权限闸门（§8.3）：工具声明的模块权限不够就
                    # **根本不执行、也不交给模型**。此前只把住数据范围，于是只有
                    # `agent:use` 的人能读到财务/成本——"工具声明风险等级"替代不了授权。
                    #
                    # 判据只有 `missing_permissions` 一处；L1/L2/L3 都要过它：
                    # L2/L3 虽然这里只是落成待确认动作，但确认与重试也是执行入口
                    # （execute_action / retry_tool_call 会再判一次）。
                    missing = missing_permissions(spec, user)
                    result = {
                        "error": (
                            f"无操作权限：需要 {' / '.join(missing)}（{spec.label}），"
                            "请找管理员分配对应模块的查看权限"
                        )
                    }
                    await _record_execution(
                        session,
                        session_id=agent_session.id,
                        action_id=None,
                        tool_name=name,
                        risk=spec.risk,
                        payload=args,
                        output=None,
                        status="failed",
                        error=result["error"],
                        user=user,
                        started_at=datetime.now(UTC),
                    )
                    tool_trace.append(
                        {
                            "tool": name,
                            "label": tool_label(name),
                            "risk": spec.risk,
                            "input": args,
                            "output": result,
                        }
                    )
                    yield (
                        "tool_call",
                        {
                            "tool": name,
                            "label": tool_label(name),
                            "risk": spec.risk,
                            "input": args,
                            "output": result,
                        },
                    )
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
                            user=user,
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
                            user=user,
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
                    yield (
                        "tool_call",
                        {
                            "tool": name,
                            "label": tool_label(name),
                            "risk": "L1",
                            "input": args,
                            "output": result,
                        },
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
                    # L2/L3 也要实时可见：让前端在模型还在说话时就能显示"准备记跟进"
                    yield (
                        "tool_call",
                        {
                            "tool": name,
                            "label": tool_label(name),
                            "risk": spec.risk,
                            "input": args,
                            "output": result,
                        },
                    )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps(result, ensure_ascii=False)[:3000],
                    }
                )
            await session.commit()

        if not reply_text:
            # 工具轮次用完了还没轮到模型说话：再要一句收尾总结（这次不给工具，逼它开口）
            try:
                summary_messages = messages + [
                    {
                        "role": "user",
                        "content": "请用两三句话总结上面的查询结果，并说明你准备了什么动作、需要我确认什么。",
                    }
                ]
                async for ev_kind, payload in chat_round(summary_messages, with_tools=False):
                    if ev_kind == "delta":
                        streamed_parts.append(payload)
                        yield ("delta", {"text": payload})
                    else:
                        reply_text = payload[0]
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
        yield (
            "done",
            {
                "reply": reply_text,
                "actions": actions_payload,
                "tool_calls": tool_trace,
            },
        )
    except GeneratorExit:
        # aclose() 允许在清理里 await：提交部分回复后把 GeneratorExit 继续外抛
        partial = "".join(streamed_parts).strip()
        if partial and not _reply_saved:
            session.add(
                AgentMessage(
                    session_id=agent_session.id,
                    role="assistant",
                    content=partial + "\n\n（连接中断，以上为已生成的部分）",
                )
            )
            if agent_session.title in ("", "新会话"):
                agent_session.title = text[:20]
            await session.commit()
        raise


async def run_turn(
    session: AsyncSession,
    *,
    agent_session: AgentSession,
    user: CurrentUser,
    text: str,
    on_event: Callable[[str, dict], None] | None = None,
) -> dict:
    """同步口径的一轮对话：消费 run_turn_events，进度回调给 on_event，返回最终结果。

    与流式端点共用同一个实现（不要写两份），`POST /agent/sessions/{id}/messages`
    和不需要打字机效果的场景走这里。
    """
    result: dict = {}
    try:
        async for kind, data in run_turn_events(
            session, agent_session=agent_session, user=user, text=text
        ):
            if on_event:
                on_event(kind, data)
            if kind == "done":
                result = data
    except AppError:
        raise
    except Exception as exc:
        # 同步口没有 SSE 的 error 事件可发：把模型侧失败（欠费/网络/限流）
        # 转成带真实原因的统一错误，而不是裸 500 纯文本
        raise AppError(
            ErrorCode.SYSTEM_ERROR, f"Agent 模型调用失败：{exc}", 500
        ) from exc
    return result


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
        if payload.get("task_due_at"):
            fields.append({"label": "下次跟进", "value": payload["task_due_at"]})
        if payload.get("exemption_reason"):
            from app.modules.followup.schema import EXEMPTION_LABELS
            fields.append({"label": "免填原因", "value": EXEMPTION_LABELS.get(payload["exemption_reason"], "未识别原因")})

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
    # 状态检查与写入不是原子的：并发双击"确认"会各自读到 awaiting_confirmation
    # 然后各执行一次写动作（记跟进变两条）。先对动作行加行锁再查状态，
    # 第二个请求会等到第一个提交后，看到的是已处理状态而被拦下。
    await session.refresh(action, with_for_update=True)
    if action.status not in ("awaiting_confirmation", "approval_required"):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该动作已经处理过了")
    spec = TOOLS.get(action.tool_name)
    if spec is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到对应的工具", 404)
    # 确认卡也是执行入口：权限可能在"AI 提议"之后被收回，
    # 所以这里必须再判一次（判据与流式那处同一份 `ToolSpec.permissions`）。
    ensure_tool_permission(spec, user)

    ctx = ToolContext(session=session, user=user, agent_session_id=action.session_id, action_id=action.id)
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
            user=user,
            started_at=started,
        )
        await session.commit()
        raise

    action.status = "executed"
    action.result = result
    action.confirmed_by = user.id
    action.confirmed_at = datetime.now(UTC)
    # 工具内部会为"业务数据变更"写一条审计；这里再单独记一条"用户确认了哪个 AI 动作"，
    # 两者缺一不可：前者能查到数据变了，后者才能回答"是哪个 AI 提议、谁点的确认"。
    await write_audit(
        session,
        operator_id=user.id,
        source="AGENT",
        action="confirm_agent_action",
        business_type=action.business_type or "agent_action",
        business_id=action.business_id,
        after={
            "agent_action_id": action.id,
            "tool_name": action.tool_name,
            "risk_level": action.risk_level,
            "payload": action.proposed_payload,
        },
    )
    await _record_execution(
        session,
        session_id=action.session_id,
        action_id=action.id,
        tool_name=action.tool_name,
        risk=action.risk_level,
        payload=action.proposed_payload or {},
        output=result,
        status="success",
        user=user,
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
    if action.tool_name == "create_followup":
        from app.modules.notification.service import dispatch_pending
        await dispatch_pending(session)
    return result


async def retry_tool_call(
    session: AsyncSession,
    *,
    execution_row: AgentExecution,
    user: CurrentUser,
    agent_session: AgentSession,
) -> dict:
    """重试一次失败的工具调用（03-API §37 Execution）。

    用**原来的入参**再跑一次同一个工具，并记一条**新的**执行记录：
    失败历史不能覆盖 —— "为什么一直失败"要靠它排查。

    只允许重试读类工具。写类工具（创建跟进/任务、提交审批）重试可能
    造成重复写入，那是业务事故而不是重试；调用方（router）已挡掉
    `status == success` 的情况，这里再挡一层写入类工具，防止"上次超时
    其实已经写成功了"这种最阴的情况。
    """
    spec = TOOLS.get(execution_row.tool_name)
    if spec is None:
        raise AppError(ErrorCode.NOT_FOUND, "找不到对应的工具", 404)
    if spec.risk != "L1":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"「{tool_label(spec.name)}」是写操作，重试可能重复写入，"
            "请重新发起而不是重试",
        )
    # 重试同样是执行入口：不给权限的人不能靠"重试之前那次失败的调用"读数据
    # （失败的执行记录里可能就带着上一次的入参，例如别人的 order_id）。
    ensure_tool_permission(spec, user)

    ctx = ToolContext(
        session=session, user=user, agent_session_id=agent_session.id
    )
    started = datetime.now(UTC)
    payload = execution_row.input_payload or {}
    try:
        result = await spec.handler(ctx, **payload)
    except AppError as exc:
        await _record_execution(
            session,
            session_id=agent_session.id,
            action_id=execution_row.action_id,
            tool_name=execution_row.tool_name,
            risk=execution_row.risk_level,
            payload=payload,
            output=None,
            status="failed",
            error=exc.message,
            user=user,
            started_at=started,
        )
        await session.commit()
        raise

    await _record_execution(
        session,
        session_id=agent_session.id,
        action_id=execution_row.action_id,
        tool_name=execution_row.tool_name,
        risk=execution_row.risk_level,
        payload=payload,
        output=result,
        status="success",
        user=user,
        started_at=started,
    )
    await session.commit()
    return {"retried_from": execution_row.id, "tool_name": spec.name, "output": result}
