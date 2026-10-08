#!/usr/bin/env python
"""第七批导入完整性：在**真 PostgreSQL 隔离库**上跑一遍接口级验收。

## 为什么还要这个脚本（离线 pytest 已经有 26 项）

`tests/test_seventh_round_imports.py` 用的是内存 SQLite。SQLite 能证明
"逐行 SAVEPOINT 让坏行不拖死好行"这类**逻辑**，但证明不了 PostgreSQL 的东西：

- 真正的 `SAVEPOINT` + 唯一约束/非空约束报错后的**事务可用性**
  （SQLite 的约束错误与 PG 的 `InFailedSqlTransaction` 不是一回事）；
- `String(255)` 超长、`Numeric` 精度、`JSONB` 这些只在 PG 上才有的行为；
- 接口层的表单参数（`preview=true` 到底有没有真的写库）。

## 覆盖的条目

- 7.1 好/坏/好三行 → 两行成功一行失败；坏行之后的好行真的落库；
- 7.2 负指导价 / 倒置区间 / 倒置有效期 / 非法数值 → 逐行报错且不落库；
- 7.4 空白成本不造成本行、外币成本被拒、只填部分存 NULL；
- 7.5 未知负责人不归到导入人、未来日期报错、预览给负责人映射表；
- 7.6 预览不写库、失败清单全量返回（**不再截断成 50/1000 条**）、
      同一行多个问题算一行失败。

跑法（必须显式给 API_BASE 与 DATABASE_URL，指到一次性隔离库）：

    cd backend
    API_BASE=http://127.0.0.1:8026/api/v1 \\
      DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5433/crm_iso_import \\
      PYTHONPATH=. .venv/bin/python scripts/check_import_integrity.py
"""

import asyncio
import json
import time
import urllib.error
import urllib.request
from _test_support import require_api_base, require_isolated_db

require_isolated_db()

import app.main  # noqa: F401  保证所有模型都注册进 metadata
from sqlalchemy import select, text

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.modules.product.model import Product, Sku
from app.modules.user.model import Department, User

FAILURES: list[str] = []
PREFIX = "CHKIMP"
STAMP = str(int(time.time()))
BASE = require_api_base()

#: 和别的套件同样的要求：不给 API_BASE 就拒跑，避免打到开发后端
if not BASE:
    raise SystemExit("必须显式设置 API_BASE（本套件会写夹具、也会真的调接口）")
if "8000" in BASE:
    raise SystemExit(f"API_BASE 指向 8000（开发后端）很可能是误传：{BASE}")


def check(label: str, actual, expected) -> None:
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: str = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}' + (f"：{detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def call(method: str, path: str, token: str | None = None, body=None, files=None):
    data = None
    headers = {}
    if files is not None:
        boundary = "----CHKIMP" + STAMP
        parts = []
        for name, (filename, content) in files.items():
            parts.append(f"--{boundary}\r\n")
            parts.append(
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            )
            parts.append("Content-Type: text/csv\r\n\r\n")
            parts.append(content)
            parts.append("\r\n")
        for name, value in (body or {}).items():
            parts.append(f"--{boundary}\r\n")
            parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n')
            parts.append(str(value))
            parts.append("\r\n")
        parts.append(f"--{boundary}--\r\n")
        data = "".join(parts).encode("utf-8")
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(BASE + path, data=data, method=method)
    for key, value in headers.items():
        req.add_header(key, value)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw.strip().startswith("{") else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw or "{}")
        except Exception:
            return exc.code, {}


def csv_upload(text_content: str):
    return {"file": ("import.csv", text_content)}


def login(username: str, password: str) -> str:
    _, res = call("POST", "/auth/login", body={"username": username, "password": password})
    if res.get("code") != 0:
        raise SystemExit(f"登录失败（{username}）：{res.get('message')}")
    return res["data"]["access_token"]


async def cleanup() -> None:
    """自底向上清干净：成本 → 价格规则 → 产品 → 客户 → 用户/角色/部门。"""
    async with SessionLocal() as session:
        await session.execute(
            text("delete from product_costs where sku_id in (select id from skus where product_id in (select id from products where name like :p))"),
            {"p": PREFIX + "%"},
        )
        await session.execute(
            text("delete from price_rules where sku_id in (select id from skus where product_id in (select id from products where name like :p))"),
            {"p": PREFIX + "%"},
        )
        await session.execute(text("delete from skus where sku_code like :p"), {"p": PREFIX + "%"})
        await session.execute(text("delete from products where name like :p"), {"p": PREFIX + "%"})
        await session.execute(text("delete from contacts where customer_id in (select id from customers where name like :p)"), {"p": PREFIX + "%"})
        await session.execute(text("delete from customers where name like :p"), {"p": PREFIX + "%"})
        await session.execute(
            text("delete from request_keys where request_key like :p"), {"p": "%" + PREFIX + "%"}
        )
        await session.execute(text("delete from users where username like :p"), {"p": PREFIX.lower() + "%"})
        await session.commit()


def in_db(coro):
    """在**独立事件循环**里跑一段数据库操作，跑完把连接池关掉。

    为什么必须 dispose：`SessionLocal` 是全局引擎，连接池里的连接绑定在
    创建它的那个事件循环上。脚本里多次 `asyncio.run(...)` 会各自新建循环，
    第二次复用池中连接就会炸在很深的 asyncpg 栈里
    （`AttributeError: 'NoneType' object has no attribute 'send'`）。
    这里每次跑完就释放连接，简单且不会互相污染。
    """
    from app.core.database import engine

    async def _run():
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(_run())


async def build_fixtures() -> dict:
    """造一套专属夹具：一个产品 + 两个 SKU + 一个停用账号。

    为什么不用 seed 的数据：套件要能独立跑、也要能自己清干净；
    用专属前缀就不会碰到别人的数据（也不会被别人清掉）。
    """
    async with SessionLocal() as session:
        product = Product(name=f"{PREFIX}产品-{STAMP}", status="active")
        session.add(product)
        await session.flush()
        good = Sku(product_id=product.id, sku_code=f"{PREFIX}-SKU-{STAMP}", status="active", unit="件")
        disabled = Sku(
            product_id=product.id, sku_code=f"{PREFIX}-OFF-{STAMP}", status="disabled", unit="件"
        )
        session.add_all([good, disabled])
        # 停用账号：用来验"停用的人不能被指定为负责人"
        session.add(
            User(
                username=f"{PREFIX.lower()}-off-{STAMP}",
                name=f"{PREFIX}停用账号",
                password_hash=hash_password("123456"),
                status="disabled",
                department_id=(
                    await session.execute(select(Department.id).limit(1))
                ).scalar(),
            )
        )
        await session.commit()
        return {"product_id": product.id, "good_sku": good.sku_code, "disabled_sku": disabled.sku_code}


def main() -> None:
    in_db(cleanup())
    fixtures = in_db(build_fixtures())
    token = login("admin", "admin123")
    good_sku = fixtures["good_sku"]

    print("== 7.6 模板对齐 + 预览不写库 + 失败清单全量 ==")
    status, res = call("GET", "/products/import-template", token)
    check("产品模板可下载", status, 200)

    rows = [f"{PREFIX}好产品一-{STAMP},包装", "X" * 300 + ",包装", f"{PREFIX}好产品二-{STAMP},包装"]
    csv_text = "产品名称,产品线\n" + "\n".join(rows) + "\n"
    _, preview = call("POST", "/products/import", token, body={"preview": "true"}, files=csv_upload(csv_text))
    check_true("预览不许写库（preview=true）", preview["data"]["preview"] is True)
    check("预览：将新增 2 条", preview["data"]["created_count"], 2)
    check("预览：失败 1 条（名称超长）", preview["data"]["failed_count"], 1)
    check_true("预览返回了快照键", bool(preview["data"].get("preview_token")))

    async def _count_products() -> int:
        """只数**导入进来的**产品。

        不能数 `CHKIMP%`：夹具自己那个产品也带这个前缀，
        于是"预览后 0 条"会永远算成 1 条 —— 断言写错比缺陷本身更难查。
        """
        async with SessionLocal() as session:
            return len(
                (
                    await session.execute(
                        select(Product.id).where(Product.name.like(f"{PREFIX}好产品%"))
                    )
                ).all()
            )

    check("预览后库里没有这两行", in_db(_count_products()), 0)

    print("== 7.1 好/坏/好三行 ==")
    _, done = call("POST", "/products/import", token, files=csv_upload(csv_text))
    check("正式导入：新增 2 条", done["data"]["created_count"], 2)
    check("正式导入：失败 1 条", done["data"]["failed_count"], 1)
    check_true("坏行之后的好行确实落库了", in_db(_count_products()) == 2)

    print("== 7.6 失败清单不再截断（60 行全错）==")
    # 产品导入按名称查重、不会失败；价格规则导入能用"找不到 SKU"稳定造出 60 行失败
    rule_rows = ["SKU编码,客户等级(留空=通用),数量下限,数量上限(留空=不限),标准价,指导价,"
                 "最低保护价,目标利润率(如0.30),生效起始日(YYYY-MM-DD),生效截止日(YYYY-MM-DD),"
                 "历史标记(填1=历史资料),备注"]
    rule_rows += [f"{PREFIX}-缺失-{STAMP}-{i},,,,,100,,,,,," for i in range(60)]
    _, many_res = call(
        "POST",
        "/price-rules/import",
        token,
        body={"preview": "true"},
        files=csv_upload("\n".join(rule_rows) + "\n"),
    )
    check("60 行失败全部返回（没有截成 50/1000）", many_res["data"]["failed_count"], 60)
    check("返回的失败条数也是 60", len(many_res["data"]["failed"]), 60)
    check("未截断标记", many_res["data"]["failed_truncated"], False)

    print("== 7.2 数值与区间校验（价格规则）==")
    bad_rows = [
        f"{good_sku},A,100,50,95,-85,70,0.30,2026-12-31,2026-10-01,,负数+倒置区间+倒置有效期",
        f"{good_sku},A,1,,,NaN,,,,,NaN 指导价",
        f"{fixtures['disabled_sku']},,,,,100,,,,,,停用 SKU",
    ]
    csv_bad = "\n".join([rule_rows[0]] + bad_rows) + "\n"
    _, bad_res = call("POST", "/price-rules/import", token, files=csv_upload(csv_bad))
    check("三行全失败", bad_res["data"]["failed_count"], 3)
    check("一行都没落库", bad_res["data"]["created_count"], 0)
    reasons = " ".join(item["reason"] for item in bad_res["data"]["failed"])
    check_true("负指导价被拒", "不能为负数" in reasons)
    check_true("倒置区间被拒", "不能大于数量上限" in reasons)
    check_true("倒置有效期被拒", "不能晚于生效截止日" in reasons)
    check_true("NaN 被拒", "不是有限数字" in reasons or "不是有效数字" in reasons)
    check_true("停用 SKU 被拒", "停用" in reasons)

    print("== 7.4 成本口径 ==")
    cost_rows = [
        f"{good_sku},,,,,CNY,2026-10-01,,四项全空",
        f"{good_sku},10,,,,USD,2026-10-01,,外币成本",
        f"{good_sku},12.5,,,,CNY,2026-10-01,,只填采购",
    ]
    csv_cost = (
        "SKU编码,采购成本,生产成本,包装成本,加工成本,币种(默认CNY),"
        "生效起始日(YYYY-MM-DD),生效截止日(YYYY-MM-DD),备注\n" + "\n".join(cost_rows) + "\n"
    )
    _, cost_res = call("POST", "/costs/import", token, files=csv_upload(csv_cost))
    check("空白与外币两行失败", cost_res["data"]["failed_count"], 2)
    check("只填部分那一行成功", cost_res["data"]["created_count"], 1)
    cost_reasons = " ".join(item["reason"] for item in cost_res["data"]["failed"])
    check_true("四项全空被拒（不造全零成本）", "全为空" in cost_reasons)
    check_true("外币被拒", "只支持人民币" in cost_reasons)

    async def _cost_null_components() -> list:
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    text(
                        "select purchase_cost, production_cost from product_costs c "
                        "join skus s on s.id = c.sku_id where s.sku_code = :code"
                    ),
                    {"code": good_sku},
                )
            ).all()
            return rows

    stored = in_db(_cost_null_components())
    check("成本行只有一条（空白那行没造出来）", len(stored), 1)
    if stored:
        check_true(
            "未填的列存 NULL（未提供 ≠ 明确为 0）",
            stored[0][0] is not None and stored[0][1] is None,
            f"purchase={stored[0][0]} production={stored[0][1]}",
        )

    print("== 7.5 历史客户导入 ==")
    future = "2099-01-01"
    off_login = f"{PREFIX.lower()}-off-{STAMP}"
    customer_rows = [
        f"{PREFIX}未知负责人客户,nobody-{STAMP},2025-01-01",
        f"{PREFIX}未来日期客户,admin,{future}",
        f"{PREFIX}停用负责人客户,{off_login},2025-01-01",
        f"{PREFIX}正常客户,admin,",
    ]
    csv_customers = "客户名称,负责人登录名,最后联系日期\n" + "\n".join(customer_rows) + "\n"
    _, cust_preview = call(
        "POST",
        "/customers/import",
        token,
        body={"preview": "true"},
        files=csv_upload(csv_customers),
    )
    check("预览：三行失败一行成功", cust_preview["data"]["failed_count"], 3)
    check("预览：新增 1 条", cust_preview["data"]["created_count"], 1)
    cust_reasons = " ".join(item["reason"] for item in cust_preview["data"]["failed"])
    check_true("未知负责人被拒（不归到导入人）", "找不到登录名" in cust_reasons)
    check_true("未来日期被拒", "未来日期" in cust_reasons)
    check_true("停用账号被拒", "已停用" in cust_reasons)
    check_true(
        "预览给了负责人映射表",
        bool(cust_preview["data"].get("owner_mapping")),
    )
    check_true(
        "没填联系日期的客户被标「联系时间未知」",
        cust_preview["data"].get("unknown_contact_count") == 1,
    )

    print("== 收尾 ==")
    in_db(cleanup())
    if FAILURES:
        print(f"✗ 失败 {len(FAILURES)} 项：" + "；".join(FAILURES))
        raise SystemExit(1)
    print("✓ 全部通过：导入逐行隔离、数值/区间/币种校验、成本空白不落库、历史客户不静默改归属")


if __name__ == "__main__":
    try:
        main()
    finally:
        in_db(cleanup())
