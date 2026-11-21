#!/usr/bin/env python
"""部分更新里的「必填字段被清空」：参数校验阶段就拒，不再 500（第十一批 11.9）。

**只在隔离库跑**：库名必须含 test，且推送开关全关、**必须显式给 API_BASE**。

## 守的是什么

复审实测三个真实接口：改产品传 `name: null`、改定制询价传 `title: null`、
改案例传 `title: null` —— 全部 **500**（参数过了接口校验，到数据库才撞非空约束）。
而清空产品描述 `description: null` 是**合理**的（可选字段），必须继续可用。

所以口径是**三种情况分开**：

1. 没传这个字段 → 保持原值（部分更新的本意）；
2. 可清空字段传 `null` → 允许清空；
3. 必填字段显式传 `null` / 空串 / 纯空白 / 超长 → **参数校验阶段拒绝**并给明确提示。

实现集中在 `app/core/patch_schema.py`：一个公共基类 + 一张"哪些字段库里非空"
的登记表（长度取**库列实际上限**）。判据集中一处，不散在 26 个 schema 里各写一遍。

## 为什么断言"原值没被改"

被拒可能是"先写进去再抛错"、靠事务回滚兜住 —— 那种实现下用户以为没改成，
其实中间态已经落过库。所以每条"被拒"都要**再读一次原值**。

## 为什么是 400 而不是 500

参数校验失败走 `RequestValidationError`，本项目在 `core/errors.py` 里统一映射成
**400**（不是 FastAPI 默认的 422），提示语里带字段的中文名；
数据库约束失败才是 500「服务器内部错误」—— 本轮修的就是把它从后者挪到前者。

跑法（隔离库）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_iso_test_xxx \\
      FILE_ROOT=data/iso-files-xxx PYTHONPATH=. .venv/bin/python \\
      scripts/check_patch_null_guard.py
"""

import asyncio
import json
import time
import urllib.error
import urllib.request
from _test_support import require_api_base, require_isolated_db

require_isolated_db()

import app.main  # noqa: F401  保证所有模型都注册进 metadata

_ = app.main  # 显式"用"一下：只 import 不带这一句，pyflakes 会当成未使用

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import String, delete, select

from app.core.audit import AuditLog
from app.core.database import SessionLocal
from app.modules.cases.model import SalesCase
from app.modules.customer.model import Contact, Customer
from app.modules.inquiry.model import CustomInquiry
from app.modules.pricing.model import PriceRule, ProductCost
from app.modules.product.model import Product, Sku
from app.modules.settings.model import NumberingRule
from app.modules.task.model import Task

# 地址与库的防呆统一收在 _test_support（判据只留一处）
BASE = require_api_base()

MARKER = f"CHKPN{int(time.time())}"
FAILURES: list[str] = []


def check(label: str, actual, expected) -> None:
    """比较式断言（本文件只用这一种签名 —— 混用会变成假绿）。"""
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}：{actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


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
        except Exception:  # noqa: BLE001
            return exc.code, {}


def login(username: str, password: str) -> str:
    _, res = call("POST", "/auth/login", body={"username": username, "password": password})
    if res.get("code") != 0:
        raise SystemExit(f"登录失败（{username}）：{res.get('message')}")
    return res["data"]["access_token"]


async def read_value(model, row_id: int, field: str):
    async with SessionLocal() as session:
        row = (await session.execute(select(model).where(model.id == row_id))).scalars().first()
        return None if row is None else getattr(row, field)


async def main():
    admin = login("admin", "admin123")

    # ⚠️ **必须在 `try` 之前初始化**（2026-10-09 审查第二次指出）。
    # 上一版我把它们放在 `try` 内的"建夹具之前"，看着像修好了，其实**仍在 try 里** ——
    # `try` 的第一条语句（`login` 之后的第一步）就抛错时，`finally` 里的收尾清理
    # 照样会 `UnboundLocalError`，把真正的报错盖掉、并中断后续清理。
    # 放进 `try` 之前的函数体，任何失败路径下 `finally` 都能安全引用它们。
    price_rule_id: int | None = None
    product_cost_id: int | None = None

    def api(method, path, body=None, expected=200):
        status, result = call(method, path, token=admin, body=body)
        assert status == expected, (method, path, status, result)
        return result.get("data")

    try:
        print("=== 0. 夹具 ===")
        product = api("POST", "/products", {"name": f"{MARKER}产品", "description": "原始描述"})
        customer = api("POST", "/customers", {"name": f"{MARKER}客户"})
        task = api("POST", "/tasks", {"title": f"{MARKER}任务"})
        inquiry = api("POST", "/custom-inquiries", {"title": f"{MARKER}询价"})
        case = api("POST", "/cases", {"title": f"{MARKER}案例"})
        print(
            f"  产品 {product['id']}、客户 {customer['id']}、任务 {task['id']}、"
            f"询价 {inquiry['id']}、案例 {case['id']}"
        )

        #: (标题, PATCH 路径, 模型, 主键, 必填字段, 中文名)
        CASES = [
            ("产品", f"/products/{product['id']}", Product, product["id"], "name", "产品名称"),
            ("客户", f"/customers/{customer['id']}", Customer, customer["id"], "name", "客户名称"),
            ("任务", f"/tasks/{task['id']}", Task, task["id"], "title", "任务标题"),
            (
                "定制询价",
                f"/custom-inquiries/{inquiry['id']}",
                CustomInquiry,
                inquiry["id"],
                "title",
                "询价标题",
            ),
            ("案例", f"/cases/{case['id']}", SalesCase, case["id"], "title", "案例标题"),
        ]

        print("\n=== 1. 必填字段传 null → 422，且原值一个字没动 ===")
        for label, path, model, row_id, field, cn in CASES:
            before = await read_value(model, row_id, field)
            status, res = call("PATCH", path, token=admin, body={field: None})
            check(f"{label}：{field}=null → 参数错误（不再是 500）", status, 400)
            check(
                f"{label}：提示里点出「{cn}不能为空」",
                cn in str(res.get("message", "")) or cn in json.dumps(res, ensure_ascii=False),
                True,
            )
            check(f"{label}：原值没被改", await read_value(model, row_id, field), before)

        print("\n=== 2. 空串 / 纯空白也拒（它们等于「把它清空了」）===")
        for label, path, model, row_id, field, _cn in CASES:
            before = await read_value(model, row_id, field)
            checked = []
            for bad in ("", "   "):
                status, _res = call("PATCH", path, token=admin, body={field: bad})
                checked.append(status)
            check(f"{label}：空串与空白都被拒", checked, [400, 400])
            check(f"{label}：原值仍没被改", await read_value(model, row_id, field), before)

        print("\n=== 3. 超长也拒（否则撞库列上限、同样是 500）===")
        status, res = call(
            "PATCH", f"/products/{product['id']}", token=admin, body={"name": "字" * 300}
        )
        check("产品名 300 字 → 参数错误", status, 400)
        check(
            "提示里给了长度上限",
            "最长" in json.dumps(res, ensure_ascii=False),
            True,
        )
        check(
            "原值仍没被改",
            await read_value(Product, product["id"], "name"),
            f"{MARKER}产品",
        )

        print("\n=== 4. 不传字段 / 可清空字段：照旧 ===")
        status, _ = call("PATCH", f"/products/{product['id']}", token=admin, body={})
        check("空 body（什么都不改）→ 200", status, 200)
        check(
            "名称保持原值",
            await read_value(Product, product["id"], "name"),
            f"{MARKER}产品",
        )
        status, _ = call("PATCH", f"/products/{product['id']}", token=admin, body={"description": None})
        check("★可选字段（产品描述）传 null → 200，允许清空", status, 200)
        check(
            "描述确实被清空了",
            await read_value(Product, product["id"], "description"),
            None,
        )
        status, _ = call(
            "PATCH", f"/customers/{customer['id']}", token=admin, body={"remark": None}
        )
        check("客户备注传 null → 200（可选字段不受影响）", status, 200)

        # ==================================================== 复审 11.9
        print("\n=== 5. 库里**可空**的字段清空不被误拦（复审 11.9）===")
        # 上一版连 required 也人工登记，于是把"库里可空"的字段当成了必填：客户行业/
        # 来源/等级、联系人职务、SKU 名称、商机来源/风险等级、用户企微账号，传 null
        # 一律白报 400 —— 用户想清空一个**允许清空**的字段，系统却说"不能为空"。
        # 现在 required 直接从库列的 `nullable` 读，人工登记与真实列不可能再分叉。
        status, res = call("POST", f"/customers/{customer['id']}/contacts", token=admin,
                           body={"name": f"{MARKER}联系人", "mobile": "13700007001"})
        check("夹具：联系人建出来了", status, 200)
        contact_id = res["data"]["id"]

        for field in ("domain", "source", "level"):
            status, _ = call("PATCH", f"/customers/{customer['id']}", token=admin,
                             body={field: None})
            check(f"客户 {field} 传 null（库里可空）→ 放行", status, 200)
        status, _ = call("PATCH", f"/contacts/{contact_id}", token=admin, body={"title": None})
        check("联系人 title 传 null（库里可空）→ 放行", status, 200)

        print("\n=== 6. 非字符串的真非空字段也不许清空（复审 11.9）===")
        # 上一版只登记了名称、状态这些**字符串**，布尔 / 数值 / 日期整类漏掉：
        # 联系人 is_primary、价格规则 min_qty、成本 effective_from 传 null 仍然 500。
        async with SessionLocal() as s:
            sku_id = (await s.execute(select(Sku.id).limit(1))).scalar_one()
            # ⚠️ 夹具必须**不与演示/其它套件的规则重叠**（2026-10-09 踩到）：
            # 原来写的是 `min_qty=0` 且不给 `customer_level`（= 全部等级），
            # 而 `seed.py` 的演示规则恰好也是"全部等级、0 ~ 不限" ——
            # ORM 直插绕过了创建时的冲突校验，于是这条夹具一出生就是重叠的；
            # 一旦对它发起 PATCH，合并后校验立刻报 40901（区间重叠），
            # 表现成"改备注被拒"。用一个专属等级 X 与实测区间避开所有已有规则。
            _rule = PriceRule(sku_id=sku_id, customer_level="X",
                              min_qty=Decimal("0"), max_qty=Decimal("999999"),
                              status="active")
            _cost = ProductCost(sku_id=sku_id, purchase_cost=Decimal("10"),
                                effective_from=datetime.now(UTC).date())
            s.add_all([_rule, _cost])
            await s.commit()
            # ⚠️ 变量名**不能叫 rule_id / cost_id**：本套件后面还有一个
            # 「编号规则」夹具也叫 `rule_id`，会把这里的值覆盖掉 ——
            # 于是收尾清理删的是编号规则，价格规则与成本永远留着（实测踩到：
            # 跑几轮后堆了 60+ 条规则，把区间占死，新夹具一建就"区间重叠"）。
            price_rule_id, product_cost_id = _rule.id, _cost.id

        status, _ = call("PATCH", f"/contacts/{contact_id}", token=admin,
                         body={"is_primary": None})
        check("联系人 is_primary（布尔非空）传 null → 400", status, 400)
        status, _ = call("PATCH", f"/price-rules/{price_rule_id}", token=admin,
                         body={"min_qty": None})
        check("价格规则 min_qty（数值非空）传 null → 400", status, 400)
        # 2026-10-09 补：价格规则的 PATCH 多了一道"不允许原地改价"的闸门
        # （价钱类字段改了要停用+新增，后端 400 并点名）。这条守住"闸门只拦价钱，
        # **不能把合法编辑一起挡住**"——只改备注必须照常放行。
        status, _ = call("PATCH", f"/price-rules/{price_rule_id}", token=admin,
                         body={"remark": "闸门不应拦备注"})
        check("价格规则只改备注 → 放行（闸门不越界）", status, 200)
        status, _ = call("PATCH", f"/costs/{product_cost_id}", token=admin,
                         body={"effective_from": None})
        check("成本 effective_from（日期非空）传 null → 400", status, 400)

        print("\n=== 7. 登记表与真实列定义的对账（复审 11.9）===")
        # 老的"类名存在"检查**查不出字段级错误**（上一版客户行业被当必填、联系人
        # is_primary 压根没登记，都是它放过去的）。现在字段判据全部来自列定义，
        # 这一节守住"登记本身没写错"：表名/类名对得上、每类至少算得出一条规则。
        from app.core.patch_schema import verify_patch_registry

        check("登记表自检无问题（表名/类名/规则覆盖）", verify_patch_registry(), [])

        print("\n=== 8. 编号规则：前缀 / 日期格式允许清空（11.9 补修）===")
        # 背景：`prefix` 留空 = 不带前缀、`date_format` 留空 = 不带日期，模型与取号逻辑
        # 本来就支持。但公共校验按"列非空"一刀切，于是新建传 "" 能过、编辑传
        # {"prefix": ""} 却报 400「编号前缀不能为空」—— 同一条规则两个入口两个答案。
        # 修法是把「不许 null」与「必须有内容」拆成两条判据。
        rule = api("POST", "/numbering-rules", {
            "code": MARKER, "name": f"{MARKER}规则",
            "prefix": "AB", "date_format": "%Y%m%d",
        })
        rule_id = rule["id"]

        status, _ = call("PATCH", f"/numbering-rules/{rule_id}", token=admin,
                         body={"prefix": ""})
        check("已有规则清空编号前缀 → 200", status, 200)
        check("前缀确实存成了空串", await read_value(NumberingRule, rule_id, "prefix"), "")

        status, _ = call("PATCH", f"/numbering-rules/{rule_id}", token=admin,
                         body={"date_format": ""})
        check("已有规则清空日期格式 → 200", status, 200)
        check("日期格式确实存成了空串",
              await read_value(NumberingRule, rule_id, "date_format"), "")

        # 「不许 null」这条照旧：库里那两列非空，传 null 仍是参数错误（不是 500）
        status, _ = call("PATCH", f"/numbering-rules/{rule_id}", token=admin,
                         body={"prefix": None})
        check("前缀传 null → 400", status, 400)
        status, _ = call("PATCH", f"/numbering-rules/{rule_id}", token=admin,
                         body={"date_format": None})
        check("日期格式传 null → 400", status, 400)
        check("被拒之后前缀没有被改动",
              await read_value(NumberingRule, rule_id, "prefix"), "")
        check("被拒之后日期格式没有被改动",
              await read_value(NumberingRule, rule_id, "date_format"), "")

        # 对照：名称这类真正必填的文字，继续拦空串与纯空白
        status, _ = call("PATCH", f"/numbering-rules/{rule_id}", token=admin,
                         body={"name": ""})
        check("规则名称清空 → 400（名称仍必须有内容）", status, 400)
        status, _ = call("PATCH", f"/numbering-rules/{rule_id}", token=admin,
                         body={"name": "   "})
        check("规则名称传纯空白 → 400", status, 400)

        # 不传的字段保持原值
        status, _ = call("PATCH", f"/numbering-rules/{rule_id}", token=admin,
                         body={"name": f"{MARKER}改名"})
        check("只改名称 → 200", status, 200)
        check("前缀仍是空串（不传就保持原值）",
              await read_value(NumberingRule, rule_id, "prefix"), "")
        check("日期格式仍是空串（不传就保持原值）",
              await read_value(NumberingRule, rule_id, "date_format"), "")

        # 历史单号不被改写：编号规则无论怎么改，都不该回头去动**已有单据上的编号**。
        # ⚠️ 不要写成"改 inquiry 那条规则" —— 编号规则表可能是空的（取号走默认值），
        # 那条分支会静默跳过，看着全绿其实没验。这里改的是本套件自建的那条规则，
        # 断言的不变量是"改规则 ≠ 改历史单据"，与规则表里有没有内容无关。
        before_no = await read_value(CustomInquiry, inquiry["id"], "inquiry_no")
        check("夹具询价确实有编号（这条断言才有意义）", bool(before_no), True)
        status, _ = call("PATCH", f"/numbering-rules/{rule_id}", token=admin,
                         body={"prefix": "ZZ", "date_format": "%Y"})
        check("再把编号规则的前缀与日期都换掉 → 200", status, 200)
        check("历史上已经生成过的单号不被改写",
              await read_value(CustomInquiry, inquiry["id"], "inquiry_no"), before_no)

    finally:
        print("\n=== 收尾清理 ===")

        async def _drop(label: str, stmt) -> None:
            try:
                async with SessionLocal() as session:
                    await session.execute(stmt)
                    await session.commit()
            except Exception as exc:  # noqa: BLE001
                print(f"  清理「{label}」失败（不影响其余）：{exc.__class__.__name__}")

        await _drop("产品", delete(Product).where(Product.name.like(f"{MARKER}%")))
        # 联系人在客户**之前**删：`contacts.customer_id` 是外键，先删客户会撞它
        await _drop("联系人", delete(Contact).where(Contact.name.like(f"{MARKER}%")))
        await _drop("客户", delete(Customer).where(Customer.name.like(f"{MARKER}%")))
        await _drop("任务", delete(Task).where(Task.title.like(f"{MARKER}%")))
        await _drop(
            "定制询价",
            delete(CustomInquiry).where(CustomInquiry.title.like(f"{MARKER}%")),
        )
        await _drop("案例", delete(SalesCase).where(SalesCase.title.like(f"{MARKER}%")))
        await _drop("编号规则", delete(NumberingRule).where(NumberingRule.code == MARKER))
        # **按 id 再兜一次**：反向验证时字段可能被改成空白（"   "），
        # 那时按名称前缀就再也匹配不到了 —— 这类残渣只能靠 id 收（本轮实打实踩到：
        # 撤掉校验后"传空白"变成 200，名字真的被写成了三个空格）。
        # ⚠️ 价格规则与成本**必须单独按 id 删**（2026-10-09 踩到）：
        # 它们存的是**标量 id**，而下面那个 id 兜底循环用 `isinstance(row, dict)`
        # 过滤，只认字典夹具 —— 标量一律被跳过（这是第一层问题）。
        # 更隐蔽的是第二层：夹具原本把价格规则的 id 也叫 `rule_id`，与本套件
        # 后面的「编号规则」夹具同名、被覆盖，于是删掉的是编号规则。
        # 两个问题叠在一起的表现是"价格规则从来没被清掉"（实测：跑几轮后堆了
        # 60+ 条规则，把区间占死，新夹具一建就"区间重叠"，原因极难看出来）。
        # 夹具没建成功时 id 是 None：跳过而不是把 None 塞进 SQL
        # （`id == None` 会生成 `IS NULL`，虽然删不到行，但语义是错的）
        if price_rule_id is not None:
            await _drop("价格规则（复审新增）",
                        delete(PriceRule).where(PriceRule.id == price_rule_id))
        if product_cost_id is not None:
            await _drop("成本（复审新增）",
                        delete(ProductCost).where(ProductCost.id == product_cost_id))
        for label, model, var in (
            ("产品", Product, "product"),
            ("客户", Customer, "customer"),
            ("任务", Task, "task"),
            ("定制询价", CustomInquiry, "inquiry"),
            ("案例", SalesCase, "case"),
        ):
            row = locals().get(var)
            if isinstance(row, dict) and row.get("id"):
                await _drop(f"{label}（按 id 兜底）", delete(model).where(model.id == row["id"]))
        await _drop(
            "审计", delete(AuditLog).where(AuditLog.after_data.cast(String).contains(MARKER))
        )
        await _drop(
            "审计（before）",
            delete(AuditLog).where(AuditLog.before_data.cast(String).contains(MARKER)),
        )
        print(f"  已清（按前缀 {MARKER} 范围收）")

    if FAILURES:
        print(f"\n失败 {len(FAILURES)} 项：{FAILURES}")
        raise SystemExit(1)
    print(
        "\nOK 部分更新入参：必填字段传 null/空白/超长一律参数错误（400）且不动原数据；"
        "不传字段保持原值；可选字段仍可清空"
    )


if __name__ == "__main__":
    asyncio.run(main())
