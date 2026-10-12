"""业务口径「只做国内 / 国内与出口都做」的唯一判据（2026-10-08）。

## 为什么要有这个文件

`trade_mode` 这个配置项 **2026-09-24 就定了**（见 `08-待领导确认清单`）：
**只做国内业务，币种固定人民币**；外贸能力留在代码里、界面上不出现，
将来要出口把口径改掉即可，不必改代码。

可是它此前**只在前端一个页面被读**（核价页拿它藏币种/汇率/退税的输入框），
后端**一处校验都没有**。于是口径是「只做国内」，但：

- 页面上看不到币种，`POST /quotes` 传 `currency=USD` 照样把美元写进库；
- 订单草稿页那个币种输入框压根没看这个开关，随手就能改成 USD；
- 统计页再把外币金额与人民币**直接相加** —— 汇总数字是错的，而且从界面上
  完全看不出来（往漏钱的方向错）。

所以把这条口径**下沉到服务层**做成一道真闸：口径是「只做国内」时，
凡是由**调用方指定**的币种一律必须是人民币，否则拒绝并说清原因。

## 三条边界（都想过，别顺手改掉）

- **只拦「调用方指定的」币种。** 从已有数据**继承**来的（订单继承报价版本、
  回款继承应收节点、商机复制自源商机）不在这里判 —— 源头已经拦住了，
  再判一次只会让历史单据彻底动不了。
- **成本另有一条更严的、与口径无关的规则**：`COST_CURRENCIES = {"CNY"}`。
  外币成本要先把汇率来源、换算时点和快照口径定下来，所以它**不受本开关影响**。
  两条规则不是一回事，不要合并。
- **外部同步（ERP）不拦**：拒收会让整批同步失败，而外部数据不是我们说了算。
  它靠统计页那句「含外币、未折算」的提醒兜底 —— 最坏情况是"看得见的不准"，
  而不是静默的错数。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode

#: 人民币。这个文件以外的代码不要自己写字面量判断，一律走下面的函数。
CNY = "CNY"

#: 口径值：只做国内。
DOMESTIC = "domestic"

#: 口径值：国内与出口都做。
#:
#: ⚠️ 这个取值**目前没有任何代码会放行外币**（见 `ensure_currency_allowed`：
#: 固定只允许 CNY）。它留着是为了将来真要做出口时有个明确的落点 ——
#: 那时要补的是"汇率来源、换算时点、快照口径"这套东西，不是把白名单改回去。
EXPORT = "export"


#: 外币记录被当成人民币用时用的固定说法（主人 2026-10-12 定的口径，原话：
#: 「只会在国内，没有外币这个东西，不要考虑这个，只要确保不会有人可以用外币
#: 这个东西钻空子就行，有人用外币就给他报错」）。
#:
#: 所以这条路上**不做折算、不做兼容**：见到非人民币就拒，并说清该找谁、该改什么。
FOREIGN_NOT_SUPPORTED = (
    "系统当前只支持人民币（CNY）：这条记录上的外币金额不会被折算，"
    "也无法参与审批与统计。请把币种改回人民币。"
    # ⚠️ 这句从前是"先请管理员把业务口径改成「国内与出口都做」"——
    # 币种改成固定白名单之后，**改配置也不放行**了，那句话会把用户指到一条
    # 走不通的路上（复审实测在核价入口看到的正是这句）。
    "（这不是改配置能放开的：当前系统不做外币业务）"
)


async def is_domestic_only(session: AsyncSession) -> bool:
    """当前业务口径是不是「只做国内」。读不到配置时按只做国内处理（更保守）。"""
    from app.modules.settings.service import get_setting

    value = await get_setting(session, "trade_mode")
    mode = str((value or {}).get("mode") or DOMESTIC).strip().lower()
    return mode == DOMESTIC


async def ensure_currency_allowed(
    session: AsyncSession, currency: str | None, *, label: str = "币种"
) -> str:
    """**只允许人民币（CNY）**；返回规范化后的币种码。

    空值/不传一律当作人民币（列默认值就是 CNY），不在这里报"必填"——
    那是别的判据的事。

    ## ⚠️ 从"看口径开关"改成"固定白名单"（第四批复审，2026-10-12）

    原来这里会先读 `trade_mode`：口径是 `domestic` 才拒外币，是 `export` 就放行。
    主人的口径已经明确了（原话）：

        「只会在国内，没有外币这个东西，不要考虑这个，只要确保不会有人可以用
          外币这个东西钻空子就行，有人用外币就给他报错」

    所以**不管 `trade_mode` 配成什么，非人民币一律拒**。留着那个分支等于留了一个
    口子：管理员把口径改成 `export`（甚至只是因为历史配置里就是 `export`），
    外币金额就能进库，然后一路参与人民币口径的审批与统计 —— 这正是
    "钻空子"。审批上下文那边已经单独拒了一道（`reject_foreign_currency`），
    但入口必须是第一道闸。

    `session` 参数**保留**：调用点很多，而且将来真要做出口时仍需要它去读口径。
    现在它不参与判断。
    """
    code = (currency or CNY).strip().upper() or CNY
    if code == CNY:
        return code
    raise AppError(
        ErrorCode.STATUS_NOT_ALLOWED,
        f"{label}只能用人民币（CNY），收到「{code}」。{FOREIGN_NOT_SUPPORTED}",
        400,
    )


def reject_foreign_currency(*currencies: str | None, label: str = "金额") -> None:
    """**只要出现外币就拒**（不做折算）。

    与 `ensure_currency_allowed` 的分工：

    - `ensure_currency_allowed` 拦在**入口**：只拦"调用方新指定的币种"，
      而且它认口径开关（口径是 export 时放行）；
    - 本函数用在**下游**（审批规则、统计汇总、差额计算），不管口径开关是什么 ——
      只要手里这条数据的币种不是人民币，就说明它是**外币记录**，
      而外币**从来没有被折算过**，拿它去算人民币口径的东西一定是错的。
      这时候宁可报错让人来处置，也不要算出一个看着像真的数。

    为什么下游还要再拦一道：口径开关只拦入口。**历史数据**里本来就可能已经有
    外币记录（口径曾经是 `export` 时建的，或 ERP 同步进来的 —— 外部同步刻意不拦），
    下游若只认"入口已经拦过了"，这些记录就会悄悄按人民币参与计算。
    """
    for currency in currencies:
        code = (currency or CNY).strip().upper() or CNY
        if code != CNY:
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                f"{label}的币种是「{code}」，{FOREIGN_NOT_SUPPORTED}",
                400,
            )


def is_foreign(*currencies: str | None) -> bool:
    """这批币种里有没有外币（判断用，不抛错）。"""
    return any(((c or CNY).strip().upper() or CNY) != CNY for c in currencies)


#: 必须调用这道闸的入口（文件 → 函数名）。
#:
#: 列的是**由调用方决定币种**的全部写入点。`check_trade_mode_gate` 用 AST 对账：
#: 这里列的每个函数体里必须真的出现 `ensure_currency_allowed`，漏一处回归就报红。
#: ⚠️ 将来新增"能选币种"的入口，**同时改这里和套件**，别只改代码。
#:
#: 为什么是这三处（2026-10-08 逐个入口确认过，不是按同类推的）：
#: - `create_quote`：报价版本币种的唯一入口，接口建单、复制报价、小助手工具都过它；
#: - `create_order`：手工建订单（`POST /orders`）直接收 `currency`；
#: - `drafts.update`：订单草稿页那个输入框，界面上唯一还能改币种的地方。
#: 不在这里的：订单/应收/回款各自的"继承"路径、成本导入（自己有更严的闸）、
#: 商机（币种不由调用方给，走列默认值）。
CURRENCY_GATE_SITES: dict[str, tuple[str, ...]] = {
    "app/modules/quote/service.py": ("create_quote",),
    "app/modules/order/service.py": ("create_order",),
    "app/modules/order/drafts.py": ("update",),
}


#: 这道闸的实现函数名。套件按这个名字做 AST 对账。
GUARD_FUNCTION = "ensure_currency_allowed"
