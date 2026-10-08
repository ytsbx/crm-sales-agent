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
import os
import time
import urllib.error
import urllib.request

import app.main  # noqa: F401  保证所有模型都注册进 metadata

_ = app.main  # 显式"用"一下：只 import 不带这一句，pyflakes 会当成未使用

from sqlalchemy import String, delete, select

from app.core.audit import AuditLog
from app.core.database import SessionLocal
from app.modules.cases.model import SalesCase
from app.modules.customer.model import Customer
from app.modules.inquiry.model import CustomInquiry
from app.modules.product.model import Product
from app.modules.task.model import Task

if not os.environ.get("API_BASE"):
    raise SystemExit(
        "必须显式设置 API_BASE（不能依赖默认的 8000，那是开发后端）：\n"
        "  API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_iso_test_xxx \\\n"
        "    PYTHONPATH=. .venv/bin/python scripts/check_patch_null_guard.py"
    )
BASE = os.environ["API_BASE"].rstrip("/")

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
        await _drop("客户", delete(Customer).where(Customer.name.like(f"{MARKER}%")))
        await _drop("任务", delete(Task).where(Task.title.like(f"{MARKER}%")))
        await _drop(
            "定制询价",
            delete(CustomInquiry).where(CustomInquiry.title.like(f"{MARKER}%")),
        )
        await _drop("案例", delete(SalesCase).where(SalesCase.title.like(f"{MARKER}%")))
        # **按 id 再兜一次**：反向验证时字段可能被改成空白（"   "），
        # 那时按名称前缀就再也匹配不到了 —— 这类残渣只能靠 id 收（本轮实打实踩到：
        # 撤掉校验后"传空白"变成 200，名字真的被写成了三个空格）。
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
