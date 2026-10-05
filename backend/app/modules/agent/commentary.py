"""专用分析的 AI 叙述（commentary）。

## 它和 insights.py 的分工

`insights.py` 算的是**事实**：逾期多少天、多久没跟进、建议价多少。
这些是确定性结果，永远可用。

这个文件只做一件事：**把已经算好的事实讲成人话**。
它是可选增强，不是能力主体 —— 所以：

- 模型没配 → `commentary` 为 `None`，并说明原因；
- 模型超时 / 报错 / 返回空 → 同上，只是说明原因不同；
- **任何情况下都不抛异常**，统计结果照常返回。

这样"回款风险"这类功能不会因为外部服务抖动就整块打不开。

## 数据出境提醒

叙述会把分析结果（客户名、金额、商机标题等）发给模型服务商。
这些字段都在用户已有权限范围内（接口本身已做数据范围过滤），
但**确实会离开本机**：不接受就设 `AGENT_COMMENTARY_ENABLED=0` 关掉这一层，
统计结果不受任何影响。
"""

import json
import logging
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

from app.core.config import settings

logger = logging.getLogger("crm.agent.commentary")

#: 没配模型时给前端的说明。以前写在 insights.py 里，跟"本地统计"混在一起；
#: 现在 commentary 相关的一切都归这个文件管。
NOT_CONFIGURED_NOTE = (
    "未配置模型（DEEPSEEK_API_KEY 为空），只返回本地统计结果；"
    "配置后这里会多一段 AI 叙述。"
)

#: 每种分析给模型的关注点。同一份结构化数据，问法不同，讲出来的话才对味 ——
#: 不给这句，模型会把"客户摘要"讲成"商机分析"，重点全错。
BRIEF: dict[str, str] = {
    "customer-summary": "这是客户摘要。讲清楚：这个客户现在什么状态、最要紧的一件事是什么、为什么是这件事。",
    "followup-suggestion": (
        "这是跟进建议。讲清楚：现在最该做的是哪一步、为什么是现在、"
        "做完之后看什么信号判断有没有效。"
    ),
    "opportunity-analysis": (
        "这是商机分析。讲清楚：这个商机走到哪一步了、卡在哪、下一步该做什么。"
    ),
    "risk-analysis": (
        "这是回款风险分析。讲清楚：风险有多大、钱压在哪、最该先催哪一笔。"
    ),
    "pricing-analysis": (
        "这是核价分析。讲清楚：这个价合不合理、最多能让到哪、什么情况下必须走审批。"
    ),
    "quote-draft": (
        "这是报价草稿建议。讲清楚：这张单大概值多少、哪几行需要留意、有没有审批风险。"
    ),
    "product-recommendation": (
        "这是产品推荐。讲清楚：优先推哪一个、依据是什么、"
        "还缺什么信息能让推荐更准。"
    ),
}

SYSTEM_PROMPT = """你是公司销售 CRM 里的分析助手，读你这段话的人是一线业务员和销售主管。

你会拿到一份**系统已经算好的结构化分析结果**，你的任务只是把它讲成人话。

纪律：
- 只能用结果里出现的数字和事实，**一个数字都不许自己编**；
- 不要逐条复述表格里已有的内容，讲重点、讲因果、讲下一步；
- 不要写小标题、项目符号、编号，写成一到两段自然段落；
- 不要说"根据您提供的数据"这类套话，直接说结论；
- 结果里没提到的东西（客户性格、市场行情、竞争对手）一律不许推测；
- **不要出现接口地址、字段名、英文键名、代码或按钮标识**；
  读你这段话的人是一线业务员和主管，他们不看代码，说业务上的话；
- 用中文，控制在 150 字以内。"""

#: 不进提示词的字段：它们是"这个结果该怎么用"的界面提示，讲流程不讲事实。
#: 喂给模型会被它当成结论照念 —— 实测踩过：报价草稿的 note 里写着「POST /quotes」，
#: 于是 AI 叙述的末尾冒出一句接口路径给业务员看。
#: 以后凡是"只给界面看的提示"，都往这里加，别指望模型自己判断要不要说。
_HIDDEN_KEYS = frozenset({"note"})

#: 送进提示词的事实上限（字符）。超了就截断 ——
#: 宁可少给一点，也不要让一次分析吃掉大量 token。
_MAX_FACTS_CHARS = 6000


def _compact(value: object, depth: int = 0) -> object:
    """把分析结果压成能进提示词的小 JSON。

    分析结果里有 ORM 对象解出来的日期、金额（Decimal）、还有几十条的
    "最近商机/最近报价"列表 —— 全塞进去既浪费 token 又冲淡重点。
    所以：日期转字符串、金额转数字、列表只留前 5 条、嵌套不超过 3 层。
    """
    if value is None or depth > 3:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="minutes")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for key, item in value.items():
            name = str(key)
            # 下划线开头是内部键；_HIDDEN_KEYS 是"只给界面看"的字段。
            # 递归生效：嵌套里同名的也一并剔掉，不用每层各判一次。
            if name.startswith("_") or name in _HIDDEN_KEYS:
                continue
            packed = _compact(item, depth + 1)
            if packed is not None:
                out[name] = packed
        return out
    if isinstance(value, Sequence):
        packed_items = [_compact(item, depth + 1) for item in list(value)[:5]]
        return [item for item in packed_items if item is not None]
    return str(value)


def _facts_text(facts: dict) -> str:
    text = json.dumps(_compact(facts), ensure_ascii=False, default=str)
    if len(text) > _MAX_FACTS_CHARS:
        text = text[:_MAX_FACTS_CHARS] + " …（内容过长，已截断）"
    return text


async def build_commentary(topic: str, facts: dict) -> tuple[str | None, str | None]:
    """生成一段 AI 叙述。

    返回 `(叙述, 说明)`：成功时说明为 `None`；没配模型或调用失败时叙述为
    `None`，说明写清原因。**绝不抛异常** —— 调用方（专用分析接口）拿到的是
    "统计结果 + 可选的叙述"，模型出任何问题都不该让统计结果跟着失败。
    """
    if not settings.agent_commentary_enabled:
        return None, "AI 叙述已关闭（AGENT_COMMENTARY_ENABLED=0），只返回本地统计结果。"
    if not settings.deepseek_api_key:
        return None, NOT_CONFIGURED_NOTE

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=settings.agent_commentary_timeout,
        )
        response = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"分析类型：{topic}\n"
                        f"{BRIEF.get(topic, '把下面的分析结果讲成人话。')}\n\n"
                        f"结构化结果：\n{_facts_text(facts)}"
                    ),
                },
            ],
            temperature=0.3,
            max_tokens=400,
            stream=False,
        )
    except Exception as exc:  # 模型侧的失败一律降级，不能拖垮统计结果
        logger.warning("AI 叙述生成失败（topic=%s）：%s", topic, exc)
        return None, f"AI 叙述生成失败：{str(exc)[:120]}（本地统计结果不受影响）"

    text = (response.choices[0].message.content or "").strip()
    if not text:
        return None, "模型没有返回叙述内容（本地统计结果不受影响）"
    return text, None
