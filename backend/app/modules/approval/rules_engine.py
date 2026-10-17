"""审批规则引擎（设计稿 `_6` 的国内业务版）。

设计稿画的是外贸口径（中信保、美元、OA 账期、Tier 客户）；本项目已确认
**只做国内**，所以条件字段全部换成国内业务的数据源，但"多条件免审 +
极速通道 + 异常路由加签 + 沙盒试算 + 版本管理"这套结构原样保留。

三类规则（kind）与生效语义：

=================  ===========================================================
kind               生效动作（条件**全部命中**时）
=================  ===========================================================
auto_pass          免审：提交即通过，不进审批流（留一条带规则痕迹的闭环审批单）
express            极速通道：跳过金额分档的高层级，一律由第一级（主管）审批
exception_route    异常加签：按金额分档走，末尾追加一个会签节点（一票否决）
=================  ===========================================================

多条规则按 `priority` 升序求值，**第一条命中的生效**（沙盒会把每条规则的
命中明细都列出来）。求值对象是「已发布且启用」的规则——草稿要发布才生效，
`enabled` 是立即生效的运维开关。
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timebase import today_business
from app.core.trade_mode import reject_foreign_currency
from app.modules.approval.model import ApprovalRule
from app.modules.payment.model import ReceivablePlan
from app.modules.payment import service as payment_service
from app.modules.quote.model import Quote, QuoteItem, QuoteVersion
from app.modules.order.model import SalesOrder

# ---------------------------------------------------------------- 条件字段目录
# 前端编辑器的字段/操作符下拉、沙盒的展示文案都以这份目录为准（单一事实来源）。
CONTEXT_FIELDS = [
    {"field": "total_amount", "label": "报价总金额", "value_type": "number", "unit": "元",
     "ops": ["gte", "lte"], "hint": "人民币口径，外币按快照汇率折算"},
    #: ⚠️ `cost_sensitive=True` = **由成本推出的字段**（第十批审查 ③，2026-10-13）。
    #: 没有 `price:manage` 的人拿到它就能反推成本：
    #: 售价 100、毛利率 80% → 成本 20。所有读出口（沙盒、审批详情）必须按权限
    #: 置空，见 `strip_cost_context`。
    {"field": "gross_margin", "label": "综合毛利率", "value_type": "number", "unit": "%",
     "ops": ["gte", "lte"], "cost_sensitive": True,
     "hint": "整单（含运费）的加权毛利率"},
    {"field": "min_item_margin", "label": "最低明细毛利率", "value_type": "number", "unit": "%",
     "ops": ["gte", "lte"], "cost_sensitive": True,
     "hint": "所有明细里最差一行的毛利率"},
    {"field": "customer_level", "label": "客户等级", "value_type": "string", "unit": "",
     "ops": ["in", "eq"], "hint": "客户档案上的等级（A/B/C/D）"},
    {"field": "customer_has_overdue", "label": "客户有逾期应收", "value_type": "bool", "unit": "",
     "ops": ["eq"], "hint": "该客户名下存在逾期状态的应收计划"},
    {"field": "concession_amount", "label": "较上一版让价金额", "value_type": "number", "unit": "元",
     "ops": ["gte", "lte"], "hint": "上一版本总额 − 本版本总额，无上一版按 0 算"},
    {"field": "payment_terms_days", "label": "账期天数", "value_type": "number", "unit": "天",
     "ops": ["gte", "lte"],
     # ⚠️ 这句提示是给配规则的人看的：识别不出时**不是** 0（现款），而是"无数据"，
     # 规则会**未命中**（2026-10-12 第四批审查 P1：从前按 0 算，导致
     # "预付30%，余款月结60天"被当成 30 天，配上"账期≤30免审"就绕过了审批）
     "hint": "从付款条件里识别的天数；识别不出时该单此字段为「无数据」，本条件不成立"},
]

OPS_LABEL = {"gte": "≥", "lte": "≤", "eq": "=", "in": "属于"}

VALID_KINDS = ("auto_pass", "express", "exception_route")


@dataclass
class RuleDecision:
    """一次命中的完整痕迹：谁命中了、命中在哪些条件、上下文是什么。"""

    rule_id: int
    rule_name: str
    kind: str
    version_no: int
    action: dict
    context: dict = field(default_factory=dict)
    matched: list = field(default_factory=list)

    def trace(self) -> dict:
        """写进 approval_instances.summary 的痕迹（要能回答"当时按哪版规则走的"）。"""
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "kind": self.kind,
            "rule_version_no": self.version_no,
            "matched_conditions": self.matched,
            "context": {k: v for k, v in self.context.items() if not k.startswith("_")},
        }


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def describe_conditions(conditions: list[dict]) -> str:
    """把条件数组写成**给用户看的一行中文**（发布确认弹窗用）。

    为什么不能用 `_cond_detail_pretty`：那个函数要**求值结果**（`hit`/`actual`），
    而发布确认时还没有具体报价可算 —— 用户要看的是"这条规则要求什么"，
    不是"某一张单命中没有"。所以这里只渲染字段名 + 运算符 + 期望值。
    """
    if not conditions:
        return ""
    parts: list[str] = []
    for cond in conditions:
        field = cond.get("field")
        meta = next((f for f in CONTEXT_FIELDS if f["field"] == field), None)
        label = (meta or {}).get("label", field)
        unit = (meta or {}).get("unit", "")
        expected = cond.get("value")
        if isinstance(expected, list):
            expected_text = "、".join(str(x) for x in expected)
        elif isinstance(expected, bool):
            expected_text = "是" if expected else "否"
        else:
            expected_text = str(expected)
        op = OPS_LABEL.get(cond.get("op", "gte"), cond.get("op", ""))
        parts.append(f"{label} {op} {expected_text}{unit}")
    return "，且 ".join(parts)


def evaluate_conditions(conditions: list[dict], ctx: dict) -> tuple[bool, list[dict]]:
    """纯函数求值：单条规则内条件 AND。返回 (是否全中, 每条条件的命中明细)。

    单独拆出来是为了沙盒与离线测试能直接调，不用碰数据库。
    """
    detail: list[dict] = []
    all_hit = True
    for cond in conditions or []:
        fld = cond.get("field")
        op = cond.get("op", "gte")
        expected = cond.get("value")
        actual = ctx.get(fld)
        meta = next((f for f in CONTEXT_FIELDS if f["field"] == fld), None)
        if meta is None or actual is None:
            all_hit = False
            detail.append({
                "field": fld, "label": (meta or {}).get("label", fld),
                "op": op, "value": expected, "actual": actual, "hit": False,
                "note": "条件字段不存在或该单缺少此数据" if meta is None else "该单此字段为空，视为未命中",
            })
            continue
        try:
            if op == "gte":
                hit = actual >= expected
            elif op == "lte":
                hit = actual <= expected
            elif op == "eq":
                hit = actual == expected
            elif op == "in":
                values = expected if isinstance(expected, list) else [expected]
                hit = actual in values
            else:
                hit = False
        except TypeError:
            hit = False
        all_hit = all_hit and hit
        detail.append({
            "field": fld, "label": meta["label"], "op": op, "value": expected,
            "actual": actual, "hit": hit,
        })
    return all_hit, detail


def _cond_detail_pretty(detail: list[dict]) -> list[dict]:
    """给前端/留痕用的可读版本：≥ 25%、≤ 100000 元 这种。"""
    pretty = []
    for d in detail:
        meta = next((f for f in CONTEXT_FIELDS if f["field"] == d["field"]), None)
        unit = (meta or {}).get("unit", "")
        pretty.append({
            **d,
            "value_label": f"{OPS_LABEL.get(d['op'], d['op'])} {_fmt(d['value'])}{unit}",
            "actual_label": f"{_fmt(d['actual'])}{unit}" if d["actual"] is not None else "无数据",
        })
    return pretty


#: 账期前面的**限定词**。只有这些词后面的数字才算账期天数。
#:
#: 为什么必须限定：`payment_terms` 是**自由文本**（`String(128)`），业务员写的是
#: "预付30%，余款月结60天"这种复合条件。从前这里抓**第一组数字**，于是：
#:
#:     预付30%，余款月结60天   → 30   （把**预付比例**当成了账期）
#:     预付款 5000 元，账期45天 → 500  （把**金额**当成了账期）
#:
#: 后果不是"数字不准"，而是**审批被绕过**：配置"账期 ≤ 30 天免审"时，
#: 真实账期 60 天的单子因为识别成 30 而走了免审（第四批审查 P1，2026-10-12）。
_TERMS_ANCHOR = (
    r"(?:账期|帐期|月结|次月结|见票后|票到|货到后|到货后|验收后|交付后|交期后|发到后|发票后|"
    r"结款|结算期|尾款后|余款后|"
    # 各式"XX 后"N 天：这些都是业务上真会写的
    r"发货后|交货后|出货后|送达后|签收后|安装后|调试后|完工后|开票后|下单后|"
    r"收款后|付款后|对账后|"
    # "XX 天后付款/付清"这类写法：锚点在**数字之后**，靠这几个词把数字锚住
    r"付款|付清|结清|付讫|付完)"
)
#: 一个分句里"限定词 + 数字 + **单位**"。
#:
#: ⚠️ **单位必须认全**（第四批复审返修，2026-10-12 实测）：
#: 只认 `天|日` 时，`账期2个月` 会被识别成 **2 天**（把"2"当数字、"个"当无关字），
#: 于是 60 天的账期被判成 2 天 → 命中"账期 ≤ 30 天免审"。月份/年份要按
#: 30 / 365 天折算（业务上没有更细的口径，宁可折算得**偏保守**）。
#: 单位写法 → 折算天数。业务上没有更细的口径，月份/年份按 30 / 365 折算；
#: 宁可折算得**偏保守**（长一点），也不要算短了让它命中免审。
_UNIT_DAYS = {
    "天": 1, "日": 1, "个月": 30, "月": 30, "年": 365,
    # "60天后付清" / "90日内" 这类：单位后面还跟了一个字，但单位还是天
    "天后": 1, "日后": 1, "天内": 1, "日内": 1, "个月后": 30, "月后": 30, "年后": 365,
}
#: 数字后面的单位（长的写前面，正则分支按顺序匹配）。
_UNIT_RE = "|".join(sorted((re.escape(u) for u in _UNIT_DAYS), key=len, reverse=True))
#: ⚠️ 拼接时**必须把锚点单独包一层组**：`_TERMS_ANCHOR` 本身是 `(?:a|b|…|余款后)`，
#: 直接把它后面接的 `[^0-9]…` 拼上去，`|` 的作用域会**泄漏到整串** ——
#: 模式变成"很长的一串 **或** 余款后"，于是 `余款60天后付清` 只匹配上"余款后"
#: 就结束了，后面的 60 天读不到（实测踩到，`余款60天后付清` 仍返回 0）。
#: 锚点与数字之间允许出现的字（"账期为"、"余款： "…）。
#:
#: ⚠️ **不能用 `[^0-9]`**：那等于允许"中间没有数字"这个条件由引擎自己挑起点来满足，
#: 于是 `余款60天后付清` 里它会把 `60` 当成"间隔字符"吃掉、再去找后面的数字，
#: 结果整句匹配失败、返回 0（实测踩到）。排除 `元` 还能顺带挡住
#: "预付款 5000 元，账期45天" 里的金额被当成账期。
_GAP = r"[^0-9元%％]{0,8}?"
_TERM_IN_CLAUSE = re.compile(
    r"(?:" + _TERMS_ANCHOR + r")" + _GAP + r"([0-9]{1,4})\s*(?:个)?(" + _UNIT_RE + r")"
)
#: 兜底：限定词后面只有数字、**连单位都没写**（如"月结30"——业务上就是 30 天）。
#: 只在同一分句里找不到带单位的写法时用。
_BARE_AFTER_ANCHOR = re.compile(
    r"(?:" + _TERMS_ANCHOR + r")" + _GAP + r"([0-9]{1,4})\s*$"
)
#: **后置**写法：`余款60天后付清` / `余款90日内付清` —— 数字和单位在前、
#: 账期词在后，中间**没有**可当锚点的词（"余款" 与 "60天" 之间是空的）。
#:
#: ⚠️ 前两版只有前置模式（`账期60天`），于是这一整类写法全都读不出来，
#: 退回"现款 0 天"（复审实测 `款到发货，余款60天后付清` → 0）。
#: 判据是"单位 + 后/内 + 付款动词"这个形状 —— 光有"N 天内"不算
#: （"预付30%，余款60天内付清"里的 60 是账期，但"质保 12 个月"不是付款条件，
#: 所以必须带上后面的付款动词）。
_UNIT_THEN_PAY = re.compile(
    r"([0-9]{1,4})\s*(?:个)?(" + _UNIT_RE + r")\s*(?:后|内|之内|以内)?\s*"
    r"(?:付款|付清|结清|付讫|付完|支付|打款|电汇|结款)"
)
#: 出现这些就当"分句里混着比例或金额"，裸数字不再采信。
_MONEY_OR_PERCENT = re.compile(
    r"[%％]|(?:预付|首付|定金|订金|货款|金额|元|万元|人民币|RMB|CNY)"
)
#: 明确表示没有账期的写法（现款/款到发货这类）。
_NO_TERMS = re.compile(
    r"(?:款到发货|货到付款|现款|现结|款到|预付全款|全款到账|一手交钱|现款现货|当面结清)"
)
_NO_TERMS_PREFIX = re.compile(
    r"(?:款到发货|货到付款|现款|现结|款到即发|款到|预付全款|全款到账|一手交钱)"
    r"[^0-9]{0,6}$"
)
#: 分句分隔符。**先分句再解析**是这份实现的关键：不分句的话，"余款60天后付清"
#: 里的 60 要跨过逗号才能找到限定词，而跨分句匹配又会把别的分句的数字吞进来。
_CLAUSE_SPLIT = re.compile(r"[，,。；;、\n]+")


def payment_terms_days(text: str | None) -> int | None:
    """从付款条件文本里识别**最长**账期天数；读不出来返回 `None`（未知）。

    返回 `None` 与返回 `0` 的差别是**安全性**差别，不是文案差别：

    - `0` = "现款、没有账期"，这是一个**事实**，可以据此匹配"账期 ≤ 30 天免审"；
    - `None` = "这段话里读不出账期"，**不能**当成现款去免审。

    规则引擎对 `None` 的处理本来就是安全的（`evaluate_conditions`：
    "该单此字段为空，视为未命中"），所以返回 None 等于"这条规则不成立"，
    单子照常走人工审批 —— 宁可多审一单，也不要因为读不准而放过。

    ## 三轮返修下来的四条规矩（每一条都有实测反例，别简化掉）

    1. **先找账期、再判现款**。一份付款条件可以同时有现款部分和账期部分
       （预付/首付是现款，余款是账期），局部出现"款到"不代表整份是现款：
       `预付30%，余款到货后60天` 从前被判成 **0**（"余款到货"里的"款到"被误伤）。
    2. **单位要认全**：`账期2个月` 从前被判成 **2 天**（只认 `天|日`）。
    3. **每个分句都要看，取最长**：`账期30天，尾款验收后90天` 从前只取第一处 **30**，
       而真实信用期是 90 天 —— 取短了就会错误免审。
    4. **同一分句里同时出现现款词与明确账期时，返回未知**：
       这种条款自相矛盾（"款到发货"又"账期 60 天"），谁也没法替业务判断哪个为准，
       交人工审批比猜一个数字安全。
    """
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None

    found: list[int] = []
    unknown = False
    cash_clause = False
    monthly_descriptor = False

    for raw_clause in _CLAUSE_SPLIT.split(s):
        clause = raw_clause.strip()
        if not clause:
            continue

        # 这一分句是不是"账期条款"（含锚点词）。后面几处都要用，所以在这里算一次。
        payment_clause = bool(re.search(_TERMS_ANCHOR + r"|余款|尾款", clause))

        # ① 明说"还没定"的写法：`另议`/`待定`/`未定`/`协商`。
        #    ⚠️ **不要求同一分句里有锚点词**（2026-10-13 修）：真实写法是
        #    `账期60天，另议` —— 分成两句，第二句只有"另议"、没有锚点。
        #    原来把它和锚点写成"与"条件，于是这几个词**从来没被拦住过**，
        #    一律走"取最长账期"返回 60，恰好是把"还在谈"当成"已定 60 天"。
        if re.search(r"另议|待定|未定|协商", clause):
            unknown = True
            continue

        # ② 本系统读不准的单位（小数、周/星期/季度/工作日/小时）：
        #    只在这个分句确实是账期条款时才当未知，避免误伤普通描述。
        if payment_clause and re.search(
            r"[0-9]+\s*[.．]\s*[0-9]+|[0-9]+\s*(?:个)?(?:周|星期|季度|工作日|小时)",
            clause,
        ):
            unknown = True
            continue

        # ① 这一分句里的账期：优先"限定词 + 数字 + 单位"
        days: list[int] = []
        for value, unit in _TERM_IN_CLAUSE.findall(clause):
            days.append(int(value) * _UNIT_DAYS[unit])
        # 后置写法：`余款60天后付清`（数字在前、账期词在后）
        for value, unit in _UNIT_THEN_PAY.findall(clause):
            days.append(int(value) * _UNIT_DAYS[unit])
        if not days:
            # 兜底：限定词后只有数字、没写单位（"月结30" —— 业务上就是 30 天）。
            #
            # ⚠️ **同一分句里混着比例/金额时，裸数字一律不采信。**
            # 这道护栏不是多余的：`预付款 5000 元` 里的"预付款"含锚点词"付款"、
            # 而"5000"正好落在锚点后面，于是裸数字路径会把它当成 **5000 天账期**。
            #
            # 提醒后来人（也是提醒我自己）：我一度以为这道护栏够不到、把它拆了，
            # 理由是"撤掉它单测全绿"。实际是**我的反向验证挑错了输入**
            # （拿 `余款30` 试，而它走的是别的分支），拆完立刻在
            # `预付款 5000 元，账期45天` 上返回 5000 —— 原始反例复活。
            # **"撤掉不变红"有时是测试没覆盖，不是代码没用。**
            if not _MONEY_OR_PERCENT.search(clause):
                days = [int(v) for v in _BARE_AFTER_ANCHOR.findall(clause)]

        is_cash = bool(_NO_TERMS.fullmatch(clause) or _NO_TERMS_PREFIX.match(clause))
        if days and is_cash:
            # ④ 自相矛盾的分句：不猜
            unknown = True
            continue
        if days:
            found.extend(days)
            continue
        if is_cash:
            cash_clause = True
        elif payment_clause and not _MONEY_OR_PERCENT.search(clause):
            if clause in ("月结", "次月结"):
                # 已有明确天数时允许付款方式说明；单独出现不能证明是现款。
                monthly_descriptor = True
            else:
                # 中文时长或按其他约定支付同样未知，不能靠“没有阿拉伯数字”放行。
                unknown = True

    if unknown:
        return None
    if found:
        # ③ 取**最长**信用期。多个账期条款时，"能拖多久"取决于最长的那条。
        return max(found)
    if monthly_descriptor:
        return None
    if cash_clause:
        # ② 整份文本里没有任何账期，且明确说了现款 —— 这是事实，是 0 天
        return 0
    return None


async def build_context(
    session: AsyncSession,
    *,
    quote: Quote,
    version: QuoteVersion,
    items: list[QuoteItem],
    fx: Decimal | None,
) -> dict:
    """把一张报价单翻译成条件求值可用的上下文（全部国内口径、人民币）。"""
    # 函数内导入：`quote.service` 在提交审批时会 import 本模块，
    # 模块级互相导入会成环。这里只要两个纯函数，没有初始化副作用。
    from app.modules.quote import service as quote_service

    foreign = (version.currency or "CNY").upper() != "CNY" and fx and fx > 0

    def to_cny(amount: Decimal) -> Decimal:
        """把**计价币种**的金额折成人民币。只用于报价侧的金额。"""
        return amount * fx if foreign else amount

    def cny_as_is(amount: Decimal) -> Decimal:
        """人民币金额原样返回。

        ⚠️ 成本类快照（`cost_snapshot` / `logistics_cost_snapshot`）**存的就是
        人民币**（见 `build_item_snapshot`：`cost_snapshot=result["cost"]["goods_cost"]`，
        从未折成计价币种）。从前这里对它们也套了 `to_cny`，外币单会把人民币成本
        **再乘一次汇率** —— 成本凭空放大，毛利率被算成一个毫无意义的数
        （2026-10-09 审查实测指出）。
        """
        return amount

    total_cny = float(to_cny(version.total_amount or Decimal(0)))
    prices = [to_cny(i.quoted_price) for i in items]
    # 产品核价成本逐版取口径（2026-10-09「产品价格与运费分离」）：
    # 新报价的运费由客户全额承担、公司原额代收代付，**不进产品利润**。
    # 判据只有 `item_unit_product_cost` 一份（统一口径后恒为"不含运费"）。
    includes_freight = quote_service.version_logistics_in_base_cost(version)
    unit_costs = [
        cny_as_is(quote_service.item_unit_product_cost(i, includes_freight=includes_freight))
        for i in items
    ]
    # ---- 整单毛利率：收入与成本**都必须按数量汇总**（2026-10-09 修）----
    #
    # 从前这里是 `sum(prices)` 与 `sum(costs)` —— 那是"**单价之和**"，却拿去减
    # "**整单**运费"，两个量纲混着算。审查实测：单价 100、成本 80、数量 100、
    # 运费 0 时毛利率 20%；只把运费改成 300，毛利率变成 **140%**
    # （内部是 200−300=−100，再与 80 比）。依赖毛利率的免审与审批规则会误判。
    #
    # 现在：成本 = Σ(单件成本 × 数量)；收入 = 整单总额 − 代收运费。
    # 用整单总额而不是 Σ(单价×数量)，是为了**保留折扣与其他费用**的处理
    # （`total_amount = subtotal + charge + discount`，只减运费即可得到非运费收入）。
    total_cost = sum(
        (c * (i.quantity or Decimal(0)) for c, i in zip(unit_costs, items)), Decimal(0)
    )
    # 运费是替客户转交给承运商的钱，不是公司的收入 —— 收入侧同样要剔除，
    # 否则"运费收得多"会被当成高毛利。
    revenue_for_margin = to_cny(version.total_amount or Decimal(0)) - to_cny(
        version.logistics_amount or Decimal(0)
    )
    # ---- 不可计算的毛利率必须是 None，不能是 0 或一个假数字（第二轮审查 P1，2026-10-12）----
    #
    # 判据与提交审批里的 `any_missing_cost` **同一份**（A06「无成本不造假」）：
    # 单件产品成本 ≤ 0 就是"没有成本记录"。两处必须同口径 —— 一边当"没成本"、
    # 一边当"成本 0"，就会出现下面这两种实测过的误判：
    #
    #   ① 缺成本、报价 50、保护价 60：成本按 0 算 → 毛利率 **100%**
    #      → 命中"毛利 ≥ 30% 免审"规则，直接自动批准；
    #   ② 产品收入 −100、成本 100：`(-100−100) / −100` = **+200%**
    #      （分母为负把符号翻正）→ 同样命中免审规则。
    #
    # 两种都是"算不出来却算出了一个好看的数字"。所以这里返回 None，
    # 由 `evaluate_conditions` 判为**未命中**（它已有"该单此字段为空，视为未命中"
    # 这条分支）：免审拿不到这个字段，就只能走正常审批；
    # 与利润无关的条件（金额、客户等级、逾期、账期）不受影响，硬拒规则也照旧生效。
    # 成本"缺失"有两层（第七批 P1-1）：
    #   ① 金额 ≤ 0（原有的"没成本"）；
    #   ② 快照标了**不完整** —— 四项成本里有 NULL，`goods_cost` 把 NULL 当 0 合计，
    #      于是"只填采购 10、其余未填"看起来是个正常的成本 10。
    #      审查实测：报价 100、保护价 120、只填采购成本 10 → 毛利率 90% →
    #      命中"毛利≥30% 免审" → **低于保护价的报价被自动批准**。
    # 两者都必须让毛利变成"未知"（None），由 `evaluate_conditions` 判未命中。
    any_missing_cost = any(cost <= 0 for cost in unit_costs) or any(
        not quote_service.item_cost_is_complete(i) for i in items
    )
    if any_missing_cost or revenue_for_margin <= 0:
        gross_margin = None
    else:
        gross_margin = float((revenue_for_margin - total_cost) / revenue_for_margin * 100)
    # 单件毛利率仍是"单件对单件"，不受上面的量纲修正影响。
    # 同样地：单价非正、成本缺失的行算不出毛利率，此时整列给 None
    # （0 会被 `lte` 类型的规则当成"毛利率 0%"而误命中）。
    if any_missing_cost or any(p <= 0 for p in prices) or not items:
        min_item_margin = None
    else:
        min_item_margin = min(
            float((p - c) / p * 100) for p, c in zip(prices, unit_costs)
        )

    customer_id = quote.customer_id

    prev_version = (
        await session.execute(
            select(QuoteVersion)
            .where(
                QuoteVersion.quote_id == quote.id,
                QuoteVersion.version_no < version.version_no,
            )
            .order_by(QuoteVersion.version_no.desc())
            .limit(1)
        )
    ).scalars().first()
    # 「较上一版让价金额」的单位是**元**（见 CONTEXT_FIELDS），所以两个版本的
    # 总额都必须是人民币才能相减。从前这里是裸的
    # `prev.total_amount - version.total_amount` —— 外币单上相减出来的是
    # **外币差额**，却被当成人民币去匹配"让价 ≤ 1000 元走极速通道"这类阈值：
    #
    #     USD 10000 → USD 9000，汇率 7：真实让价 人民币 7000，
    #     这里算出来 1000 → 命中"≤1000"，本该人工审的单子走了极速通道
    #     （第四批审查 P1，2026-10-12）。
    #
    # 主人 2026-10-12 定的口径是**不做折算**：「只会在国内，没有外币这个东西…
    # 有人用外币就给他报错」。所以这里见到外币直接拒，而不是乘个汇率糊过去 ——
    # 折算要定汇率来源与时点，那是另一件没定的事；在定下来之前，
    # 报错比算一个看着像真的数安全。
    reject_foreign_currency(
        version.currency, prev_version.currency if prev_version else None,
        label="报价版本总额",
    )
    concession = 0.0
    if prev_version is not None:
        concession = max(
            0.0, float((prev_version.total_amount or 0) - (version.total_amount or 0))
        )

    # 「客户有逾期应收」条件（2026-10-13 口径统一）：
    # 判定按**余额 + 到期日**，不再只看节点状态列 —— 状态列可能因"时间自己走过、
    # 没人写库"而不准，而"收过部分款 + 已过期"这类正是要被抓出来的风险
    # （改前它显示"部分回款"、条件不命中，大额未收就被放过）。
    # 与 payment/analytics/AI 共用同一口径（payment.service.is_plan_overdue）。
    overdue_exists = (
        await session.execute(
            select(ReceivablePlan.id)
            .join(SalesOrder, SalesOrder.id == ReceivablePlan.order_id)
            .where(
                SalesOrder.customer_id == customer_id,
                SalesOrder.status != "cancelled",
                payment_service.plan_overdue_expression(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    from app.modules.customer.model import Customer

    customer_row = await session.get(Customer, customer_id) if customer_id else None

    return {
        "total_amount": total_cny,
        # None = 不可计算（缺成本 / 产品收入非正 / 没有明细）。**不要**把它折成 0：
        # `evaluate_conditions` 对 None 的判据是"未命中"，折成 0 就变成"毛利率 0%"，
        # 那正是本轮要修的两类误判（缺成本当 100%、负收入当 200%）的另一面。
        "gross_margin": round(gross_margin, 2) if gross_margin is not None else None,
        "min_item_margin": (
            round(min_item_margin, 2) if min_item_margin is not None else None
        ),
        "customer_level": (customer_row.level if customer_row else None) or "",
        "customer_has_overdue": overdue_exists is not None,
        "concession_amount": concession,
        "payment_terms_days": payment_terms_days(version.payment_terms),
        # 展示辅助（不参与求值，沙盒直接展示）
        "_quote_no": quote.quote_no,
        "_version_no": version.version_no,
        "_customer_name": customer_row.name if customer_row else None,
        # 展示辅助（不参与求值，沙盒直接展示）。原来用 `date.today()` —— 那跟着
        # **宿主机时区**走，同一套规则在 UTC 机器和北京机器上会显示不同的"今天"。
        "_today": today_business().isoformat(),
    }


#: 由成本推出的上下文键（从 `CONTEXT_FIELDS` 的 `cost_sensitive` 标记派生，
#: 保证"注册表里标了就会脱敏"，不会出现"加了新字段忘了加进脱敏名单"）。
COST_SENSITIVE_FIELDS: tuple[str, ...] = tuple(
    f["field"] for f in CONTEXT_FIELDS if f.get("cost_sensitive")
)

#: 规则条件里涉及这些字段时，命中详情里的**实际值**也要一并隐去
#: （它同样是"由成本推出的数"，留着等于没脱敏）。
COST_SENSITIVE_SET = frozenset(COST_SENSITIVE_FIELDS)

#: 脱敏后这些字段显示成什么。**置空而不是删键**：
#: 规则求值与前端渲染都按固定键集工作，删键会让前端显示成"字段不存在"，
#: 看起来像规则配置坏了；`None` 的既有语义就是"无数据/未命中"。
COST_HIDDEN = None


def strip_cost_context(ctx: dict, *, can_see_cost: bool) -> dict:
    """按权限抹掉上下文里**由成本推出**的字段（返回副本，不改原对象）。

    ## 为什么必须有（第十批审查 ③）

    实测：只有 `quote:view`、没有 `price:manage` 的账号调规则沙盒，能拿到
    `gross_margin = 80.0`；他手上又有售价（`total_amount` 就在同一个返回里），
    于是成本 = 售价 ×(1−毛利率) 直接算出来。审批详情那条出口同理
    （返回 `profit_rate` 与含"利润率 80.00%"的文字原因）。

    报告点名的四类泄漏，逐条核过实际出口（2026-10-13）：
      ① 结构化字段（`gross_margin` / `min_item_margin`）—— **确认泄漏**，这里擦掉；
      ② 规则条件回显（`conditions_detail` 里的 `actual`）—— **确认泄漏**，这里擦掉；
      ③ 成本派生底价 —— 沙盒**不返回**底价；报价侧由
         `quote.service.strip_comparison_cost` / `serialize_item(can_see_cost=False)` 管；
      ④ 文字原因 —— 提交审批重判会写实际利润率和最低允许价。
         审批 summary 由 `strip_approval_summary` 裁剪；报价明细的文字原因
         与结构化成本一起由 `serialize_item(can_see_cost=False)` 隐藏。
         命中条件的 actual / actual_label 同样敏感，不能只清 context。

    ## 为什么返回副本

    原始上下文仍是审批依据（规则求值已经用它跑完了），
    只把**给这个人看的那份**擦掉；审计与后续审批拿到的还是原值。
    """
    if can_see_cost:
        return ctx
    stripped = dict(ctx)
    for name in COST_SENSITIVE_FIELDS:
        if name in stripped:
            stripped[name] = COST_HIDDEN
    # 条件命中详情里的实际值：命中成本敏感字段时把 actual 抹掉（保留规则与阈值，
    # 否则沙盒"哪条规则命中"这个用途就废了）。
    for key in list(stripped):
        if not key.endswith("_detail") and key != "matched_conditions":
            continue
        rows = stripped.get(key)
        if not isinstance(rows, list):
            continue
        cleaned = []
        for row in rows:
            if isinstance(row, dict) and row.get("field") in COST_SENSITIVE_SET:
                row = {**row, "actual": None, "actual_label": "无权限查看"}
            cleaned.append(row)
        stripped[key] = cleaned
    stripped["_cost_hidden"] = True
    return stripped


def strip_approval_summary(summary: dict | None, *, can_see_cost: bool) -> dict | None:
    """Hide cost-derived approval evidence in a copy; retain the stored decision facts."""
    if summary is None or can_see_cost:
        return summary
    from copy import deepcopy
    safe = deepcopy(summary)
    for row in safe.get("offending") or []:
        if isinstance(row, dict):
            for sensitive_name in ("minimum_price", "profit_rate"):
                if sensitive_name in row:
                    row[sensitive_name] = None
            reason = row.get("reason")
            if isinstance(reason, str) and any(term in reason for term in ("利润率", "最低允许价", "最低保护价", "成本")):
                row["reason"] = "报价超出价格权限，需要审批"
    if isinstance(safe.get("reason"), str) and any(term in safe["reason"] for term in ("利润率", "最低允许价", "最低保护价", "成本")):
        safe["reason"] = "报价超出价格权限，需要审批"
    trace = safe.get("rule_trace")
    if isinstance(trace, dict):
        safe["rule_trace"] = strip_cost_context(trace, can_see_cost=False)
        if isinstance(trace.get("context"), dict):
            safe["rule_trace"]["context"] = strip_cost_context(trace["context"], can_see_cost=False)
    return safe


async def load_active_rules(session: AsyncSession) -> list[tuple[ApprovalRule, dict]]:
    """取「已发布且启用」的规则及其**已发布版本快照**，按优先级排序。

    求值永远用发布时的快照，草稿改了没发布不影响线上。
    """
    rules = (
        await session.execute(
            select(ApprovalRule)
            .where(ApprovalRule.enabled.is_(True), ApprovalRule.published_version_no > 0)
            .order_by(ApprovalRule.priority.asc(), ApprovalRule.id.asc())
        )
    ).scalars().all()
    from app.modules.approval.model import ApprovalRuleVersion

    result: list[tuple[ApprovalRule, dict]] = []
    for rule in rules:
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
        if snapshot is not None:
            result.append((rule, snapshot.payload))
    return result


async def route(
    session: AsyncSession,
    *,
    quote: Quote,
    version: QuoteVersion,
    items: list[QuoteItem],
    fx: Decimal | None,
) -> RuleDecision | None:
    """提交审批时的路由判定：返回第一条命中的规则；没有规则命中返回 None（走原金额分档）。"""
    active = await load_active_rules(session)
    if not active:
        return None
    ctx = await build_context(session, quote=quote, version=version, items=items, fx=fx)
    for rule, payload in active:
        matched, detail = evaluate_conditions(payload.get("conditions") or [], ctx)
        if matched:
            return RuleDecision(
                rule_id=rule.id,
                rule_name=payload.get("name") or rule.name,
                kind=payload.get("kind") or rule.kind,
                version_no=rule.published_version_no,
                action=payload.get("action") or {},
                context=ctx,
                matched=_cond_detail_pretty(detail),
            )
    return None


def effect_summary(kind: str, action: dict) -> str:
    """一句话说明命中后果（审批记录/沙盒共用）。"""
    if kind == "auto_pass":
        return "免审：提交即通过，无需人工审批"
    if kind == "express":
        return "极速通道：跳过金额分档高层级，由第一级（主管）直接审批"
    if kind == "exception_route":
        label = action.get("add_node_label") or "财务会签"
        return f"异常加签：正常分档审批后，追加「{label}」，由有财务会签权限的人处理，一票否决"
    return kind


def serialize_rule(rule: ApprovalRule, *, has_draft_changes: bool | None = None) -> dict:
    data = {
        "id": rule.id,
        "name": rule.name,
        "kind": rule.kind,
        "priority": rule.priority,
        "enabled": rule.enabled,
        "conditions": rule.conditions,
        "action": rule.action,
        "description": rule.description,
        "published_version_no": rule.published_version_no,
        "published_at": rule.published_at,
        "updated_at": rule.updated_at,
        "effect": effect_summary(rule.kind, rule.action),
        "conditions_pretty": _cond_detail_pretty(
            [{"field": c.get("field"), "op": c.get("op"), "value": c.get("value"), "actual": None}
             for c in rule.conditions or []]
        ),
    }
    if has_draft_changes is not None:
        data["has_draft_changes"] = has_draft_changes
    return data
