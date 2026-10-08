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

#: 拒绝时的固定说法。放在常量里是为了让测试能钉住"原因说清了"，
#: 而不是各处自己编一句、改一处漏一处。
DOMESTIC_ONLY_HINT = "要用外币报价，得先请管理员把业务口径改成「国内与出口都做」"


async def is_domestic_only(session: AsyncSession) -> bool:
    """当前业务口径是不是「只做国内」。读不到配置时按只做国内处理（更保守）。"""
    from app.modules.settings.service import get_setting

    value = await get_setting(session, "trade_mode")
    mode = str((value or {}).get("mode") or DOMESTIC).strip().lower()
    return mode == DOMESTIC


async def ensure_currency_allowed(
    session: AsyncSession, currency: str | None, *, label: str = "币种"
) -> str:
    """口径是「只做国内」时，非人民币一律拒绝；返回规范化后的币种码。

    空值/不传一律当作人民币（列默认值就是 CNY），不在这里报"必填"——
    那是别的判据的事。
    """
    code = (currency or CNY).strip().upper() or CNY
    if code == CNY:
        return code
    if await is_domestic_only(session):
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"当前业务口径是「只做国内」，{label}只能用人民币（CNY），"
            f"收到「{code}」。{DOMESTIC_ONLY_HINT}",
            400,
        )
    return code


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
