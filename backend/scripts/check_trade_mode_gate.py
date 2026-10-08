"""业务口径「只做国内」的闸 + 统计汇总遇外币必须说出来（2026-10-08）。

**只在隔离库跑**：必须**显式**给 `API_BASE` 与一次性库的 `DATABASE_URL`。
本套件会真建订单草稿、真插一条外币订单、**还会临时把 `trade_mode` 改成
「国内与出口都做」再改回来**（所以更要隔离；改不回来会污染后续套件 ——
后面所有套件都会以为可以写外币）。

## 为什么要有这个套件

文档 `08-待领导确认清单` 2026-09-24 就定了：**只做国内业务，币种固定人民币**，
"外贸能力保留在代码里但界面不显示，将来改 `trade_mode` 即可"。

实际落地**只做了半截**：`trade_mode` 全项目只有两处被读（下发配置、
核价页拿它藏输入框），**后端一处校验都没有**。于是：

- 页面上看不到币种，`POST /quotes` 传 `currency=USD` 照样把美元写进库；
- 订单草稿页那个币种框压根不看开关（界面上唯一还能改币种的地方）；
- 统计页再把外币金额与人民币**直接相加**：汇总数字是错的，而且看不出来。

这一批把口径做成**服务层的一道真闸**（`app/core/trade_mode.py`），
并给统计汇总加一句**兜底提醒**（折算要等汇率口径定下来，但"说出来"现在就能做）。

## 钉住这些

1. **AST 对账**：登记表 `CURRENCY_GATE_SITES` 里每个写入函数都真的调了闸；
   登记的文件/函数不存在也报红（防止"登记了一个不存在的函数"这种假绿）
2. 口径「只做国内」时：**建报价**传 USD → 400 + `40002`，且提示里说清了原因
3. 同上：**手工建订单**传 USD → 400；**订单草稿改币种** → 400，且原值没被动
4. 传 CNY / 不传币种照旧（闸不能顺手把正常路径也拦了）
5. 口径改成「国内与出口都做」后，**同一请求不再被闸拦**（闸认配置、不写死）
6. 改回「只做国内」后闸**立刻**恢复（配置每次现读，不是启动时缓存）
7. 统计：库里没有外币时 `currency_warnings` **不出现**；
   插一条外币订单后 **出现**，且两个接口给的是**同一句话**（判据只有一处）
8. **提醒跟着数据范围走**：别人名下的外币单，不该给看不到它的业务员弹提示
9. 删掉外币订单后提醒消失（不是"出现过一次就永远贴着"）

跑法：

    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 \\
      DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_iso_test_a \\
      PYTHONPATH=. .venv/bin/python scripts/check_trade_mode_gate.py
"""

import asyncio
import ast
import json
import os
import pathlib
import urllib.error
import urllib.request
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select, text

BASE = (os.environ.get("API_BASE") or "").rstrip("/")
PREFIX = "CHKTRADEMODE" + uuid4().hex[:6]
FAILURES: list[str] = []

#: 后端仓库根（脚本在 backend/scripts 下）
APP_DIR = pathlib.Path(__file__).resolve().parent.parent

#: 套件开跑时审计表的最大 id。收尾时把这条之后写的审计全删掉 ——
#: 本套件是隔离库上唯一的写方，所以"这个 id 之后"就是"本次写的"。
#: 用 id 而不是按业务类型删，是因为改口径那条审计里**不含任何前缀**，
#: 按前缀根本找不到它。
START_AUDIT_ID = 0


def check(label: str, actual, expected) -> None:
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: object = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}' + (f"：{detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def require_isolated_db() -> str:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise SystemExit(
            "必须显式设置 DATABASE_URL（一次性隔离库，库名以 crm_iso / crm_check 开头）"
        )
    name = url.rsplit("/", 1)[-1].split("?")[0]
    if not name.startswith(("crm_iso", "crm_check")):
        raise SystemExit(f"拒绝执行：DATABASE_URL 指向 {name!r}，不是一次性隔离库")
    if not BASE:
        raise SystemExit(
            "必须显式设置 API_BASE（默认的 8000 是开发后端）。"
            "例：API_BASE=http://127.0.0.1:8001/api/v1"
        )
    return name


def call(method: str, path: str, token: str | None = None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode() or "{}")
        except Exception:
            return exc.code, {}


def login(username: str, password: str = "123456") -> str:
    _, res = call("POST", "/auth/login", body={"username": username, "password": password})
    if res.get("code") != 0:
        raise SystemExit(f"登录失败（{username}）：{res.get('message')}")
    return res["data"]["access_token"]


def can_login(username: str, password: str = "123456") -> bool:
    _, res = call("POST", "/auth/login", body={"username": username, "password": password})
    return res.get("code") == 0


# ---------------------------------------------------------------- 1) AST 对账


def assert_gate_registry() -> None:
    """登记表里说"必须调闸"的函数，真的调了闸吗。

    这是**防漂移**用的：将来有人把某个入口的闸删掉、或者新写一个"能选币种"
    的入口却忘了登记，这里必须报红。只看登记过的位置，不做全库猜测 ——
    猜出来的清单只会天天误报，最后没人看。
    """
    from app.core.trade_mode import CURRENCY_GATE_SITES, GUARD_FUNCTION

    listed = sum(len(names) for names in CURRENCY_GATE_SITES.values())
    print(f"  登记要过闸的写入函数共 {listed} 处：")
    for rel, names in CURRENCY_GATE_SITES.items():
        path = APP_DIR / rel
        check_true(f"{rel} 存在", path.exists(), str(path))
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name in names:
            func = next(
                (
                    node
                    for node in ast.walk(tree)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == name
                ),
                None,
            )
            check_true(f"{rel}::{name} 存在", func is not None)
            if func is None:
                continue
            calls_guard = any(
                isinstance(node, ast.Call)
                and (
                    (isinstance(node.func, ast.Name) and node.func.id == GUARD_FUNCTION)
                    or (isinstance(node.func, ast.Attribute) and node.func.attr == GUARD_FUNCTION)
                )
                for node in ast.walk(func)
            )
            check_true(f"{rel}::{name} 调了 {GUARD_FUNCTION}", calls_guard)

    check_true(
        "登记表非空（空表会让上面所有对账变成空转）",
        listed >= 3,
        f"共 {listed} 处",
    )


# ------------------------------------------------------------- 2..6) 口径闸


async def make_draft(admin_id: int, customer_id: int) -> int:
    """造一条订单草稿夹具（界面上唯一还能改币种的那条路）。

    走 ORM 而不是走"接受报价 → 确认 → 建草稿"的全流程：本套件要验的是**闸**，
    不是草稿怎么来的；把报价流程整套铺出来只会让这条断言更脆。
    """
    from app.core.database import SessionLocal
    from app.modules.order.model import OrderDraft, OrderDraftItem

    async with SessionLocal() as s:
        draft = OrderDraft(
            customer_id=customer_id,
            opportunity_id=None,
            owner_id=admin_id,
            source_context={"no": f"{PREFIX}-SRC", "version": 1},
            currency="CNY",
            request_key=str(uuid4()),
            request_hash="0" * 64,
            revision=1,
            created_by=admin_id,
        )
        s.add(draft)
        await s.flush()
        s.add(
            OrderDraftItem(
                draft_id=draft.id,
                source_snapshot={
                    "source_item_id": 1,
                    "unit_price": "10.0000",
                    "name": f"{PREFIX}明细",
                    "specification": None,
                    "remark": None,
                },
                name=f"{PREFIX}明细",
                quantity=Decimal("1"),
                unit_price=Decimal("10"),
            )
        )
        await s.commit()
        return draft.id


async def assert_gate() -> tuple[str | None, int]:
    """返回 (业务员的 token 或 None, 客户 id)。"""
    from app.core.database import SessionLocal
    from app.modules.customer.model import Customer
    from app.modules.order.model import OrderDraft
    from app.modules.user.model import User

    admin = login("admin", "admin123")

    print("\n── 2) 建报价：只做国内时不许美元 ──")
    status, body = call("POST", "/quotes", token=admin, body={"currency": "USD"})
    check("建报价传 USD → 400", status, 400)
    check("错误码是 40002（口径不允许，不是参数格式错）", body.get("code"), 40002)
    check_true(
        "提示说清了是「只做国内」造成的",
        "只做国内" in (body.get("message") or ""),
        body.get("message"),
    )

    print("\n── 3) 手工建订单：同样不许美元 ──")
    async with SessionLocal() as s:
        customer = (
            await s.execute(select(Customer).where(Customer.deleted_at.is_(None)).limit(1))
        ).scalars().first()
        admin_id = (
            await s.execute(select(User.id).where(User.username == "admin"))
        ).scalar_one()
    check_true("取到客户夹具", customer is not None)
    # 明细故意给一个不存在的 SKU：这样一旦闸**没**拦住，后面也会因为 SKU 不存在
    # 而失败，**不会真建出一张订单**（否则这张 USD 单会留在库里污染后面的套件）。
    order_body = {
        "customer_id": customer.id,
        "currency": "USD",
        "items": [{"sku_id": 99999999, "quantity": 1, "unit_price": "10"}],
    }
    status, body = call("POST", "/orders", token=admin, body=order_body)
    check("手工建订单传 USD → 400", status, 400)
    check("错误码同样是 40002", body.get("code"), 40002)

    print("\n── 4) 订单草稿改币种：界面上唯一还能改币种的地方 ──")
    draft_id = await make_draft(admin_id, customer.id)
    status, body = call(
        "PATCH",
        f"/order-drafts/{draft_id}",
        token=admin,
        body={"revision": 1, "currency": "USD", "items": [{"source_item_id": 1, "quantity": 1}]},
    )
    check("草稿改币种为 USD → 400", status, 400)
    check("错误码同样 40002", body.get("code"), 40002)
    async with SessionLocal() as s:
        row = await s.get(OrderDraft, draft_id)
        check("被拒之后草稿币种一个字没动", row.currency, "CNY")

    print("\n── 5) 正常路径不能被顺手拦掉 ──")
    status, body = call(
        "PATCH",
        f"/order-drafts/{draft_id}",
        token=admin,
        body={"revision": 1, "currency": "CNY", "items": [{"source_item_id": 1, "quantity": 2}]},
    )
    check("草稿改币种为 CNY → 200", status, 200)

    print("\n── 6) 口径改成「国内与出口都做」→ 同一请求不再被拦 ──")
    status, body = call(
        "PATCH", "/settings", token=admin,
        body={"key": "trade_mode", "value": {"mode": "both"}},
    )
    check("改口径 → 200", status, 200)
    status, body = call("POST", "/quotes", token=admin, body={"currency": "USD"})
    check_true(
        "口径放开后，「只做国内」这条拒绝消失了",
        body.get("code") != 40002,
        f'code={body.get("code")} message={body.get("message")}',
    )
    status, body = call("POST", "/orders", token=admin, body=order_body)
    check_true(
        "手工建订单也不再因口径被拦",
        body.get("code") != 40002,
        f'code={body.get("code")} message={body.get("message")}',
    )

    print("\n── 7) 改回「只做国内」→ 立刻恢复（配置不是启动时缓存）──")
    restore_domestic(admin)
    status, body = call("POST", "/quotes", token=admin, body={"currency": "USD"})
    check("改回来后又被拦 → 400", status, 400)
    check("还是 40002", body.get("code"), 40002)

    zhangsan = login("zhangsan") if can_login("zhangsan") else None
    return zhangsan, customer.id


def restore_domestic(admin_token: str) -> None:
    """把口径改回「只做国内」。**任何中途失败都必须执行到这一步。**"""
    call(
        "PATCH", "/settings", token=admin_token,
        body={"key": "trade_mode", "value": {"mode": "domestic"}},
    )


# --------------------------------------------------------- 8..11) 统计提醒


async def assert_currency_note(zhangsan_token: str | None, customer_id: int) -> None:
    from app.core.database import SessionLocal
    from app.modules.order.model import SalesOrder
    from app.modules.user.model import User

    admin = login("admin", "admin123")

    def notes_of(token: str) -> list:
        _, res = call("GET", "/dashboard/summary", token=token)
        return (res.get("data") or {}).get("currency_warnings") or []

    print("\n── 8) 库里没有外币时不出现提醒 ──")
    check_true("干净库上不出现 currency_warnings", notes_of(admin) == [], notes_of(admin))

    async with SessionLocal() as s:
        admin_id = (
            await s.execute(select(User.id).where(User.username == "admin"))
        ).scalar_one()
        order = SalesOrder(
            order_no=f"{PREFIX}-USD",
            customer_id=customer_id,
            owner_id=admin_id,
            sales_owner_id=admin_id,
            total_amount=Decimal("100"),
            currency="USD",
            status="pending",
            created_by=admin_id,
        )
        s.add(order)
        await s.commit()
        order_id = order.id

    print("\n── 9) 有外币时必须说出来，而且两个接口是同一句话 ──")
    admin_notes = notes_of(admin)
    check_true("admin 的工作台汇总出现了提醒", len(admin_notes) == 1, admin_notes)
    check_true(
        "提醒里说清了「未折算」",
        "未做折算" in (admin_notes[0] if admin_notes else ""),
        admin_notes,
    )
    _, res = call("GET", "/analytics/receivables", token=admin)
    receivable_notes = (res.get("data") or {}).get("currency_warnings") or []
    # 先钉"这个接口也带了"，再钉"两边一样" —— 否则两边都为空时下面那条会**空过**
    check_true("应收分析也带上了提醒", len(receivable_notes) == 1, receivable_notes)
    check_true(
        "且两个接口是同一句话（判据只有一处）",
        receivable_notes == admin_notes,
        f"summary={admin_notes} receivables={receivable_notes}",
    )

    print("\n── 10) 提醒跟着数据范围走 ──")
    if zhangsan_token:
        zs_notes = notes_of(zhangsan_token)
        check_true(
            "看不到这笔单的业务员不该被弹提示",
            zs_notes == [],
            f"zhangsan 看到 {zs_notes}",
        )
    else:
        print("  （跳过：zhangsan 登录不上）")

    print("\n── 11) 外币没了，提醒也要消失（不是贴上去就撕不掉）──")
    async with SessionLocal() as s:
        await s.execute(text("delete from sales_orders where id = :i"), {"i": order_id})
        await s.commit()
    check_true("删掉外币单后提醒消失", notes_of(admin) == [], notes_of(admin))


async def cleanup() -> None:
    """清干净本套件写下的东西，并把口径恢复成 domestic（最重要）。"""
    from app.core.database import SessionLocal

    async with SessionLocal() as s:
        await s.execute(
            text("delete from sales_orders where order_no like :p"), {"p": f"{PREFIX}%"}
        )
        await s.execute(
            text(
                "delete from order_draft_items where draft_id in"
                " (select id from order_drafts where source_context::text like :m)"
            ),
            {"m": f"%{PREFIX}%"},
        )
        await s.execute(
            text("delete from order_drafts where source_context::text like :m"),
            {"m": f"%{PREFIX}%"},
        )
        # 改口径那两条审计里不含任何前缀，按 id 之后全删（本套件是隔离库上唯一的写方）
        await s.execute(text("delete from audit_logs where id > :i"), {"i": START_AUDIT_ID})
        await s.commit()

        mode = (
            await s.execute(
                text("select value->>'mode' from system_settings where key = 'trade_mode'")
            )
        ).scalar_one_or_none()
        left = (
            await s.execute(text("select count(*) from sales_orders where currency <> 'CNY'"))
        ).scalar_one()
        drafts = (
            await s.execute(
                text("select count(*) from order_drafts where source_context::text like :m"),
                {"m": f"%{PREFIX}%"},
            )
        ).scalar_one()
        print(
            f"\n清算完成：口径={mode!r}（必须是 domestic）· 残留外币订单={left} · 残留草稿={drafts}"
        )


async def main() -> None:
    global START_AUDIT_ID

    require_isolated_db()
    from app.core.database import SessionLocal

    async with SessionLocal() as s:
        START_AUDIT_ID = int(
            (await s.execute(text("select coalesce(max(id), 0) from audit_logs"))).scalar_one()
        )

    admin = login("admin", "admin123")
    print("── 1) 登记表与调用点的 AST 对账 ──")
    assert_gate_registry()

    try:
        zhangsan_token, customer_id = await assert_gate()
        await assert_currency_note(zhangsan_token, customer_id)
    finally:
        # 口径一定改回来：留着 both 会让后面的套件以为可以写外币
        restore_domestic(admin)
        await cleanup()

    if FAILURES:
        print(f"\n共 {len(FAILURES)} 项失败：")
        for name in FAILURES:
            print("  -", name)
        raise SystemExit(1)
    print("\nOK 口径闸：三处入口都拦住了非人民币，配置一改就放开、改回就恢复；")
    print("OK 统计提醒：有外币必说、范围外不弹、删了会消失，且两个接口同一句话。")


if __name__ == "__main__":
    asyncio.run(main())
