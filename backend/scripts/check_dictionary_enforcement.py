#!/usr/bin/env python3
"""受控字典必须在**每一条能写它的路**上都生效。

用法（一次性隔离库；库名要含 test）：
    API_BASE=http://127.0.0.1:8001/api/v1 \
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_check_test_dict \
    PYTHONPATH=. .venv/bin/python scripts/check_dictionary_enforcement.py

## 为什么专门写这一套

2026-10-13 接手别人做好的"数据字典接入业务"时实测发现：

| 路径 | 传字典外的分类 | 结果 |
| --- | --- | --- |
| `POST /products`（单个新建） | `纸箱` | **400 拒绝** ✅ |
| `POST /products/import`（批量导入） | `纸箱` | **200 成功、库里真写进去了** ❌ |

也就是**两把尺子量同一件事**：受控字典只在单个入口生效，留个 Excel 后门就能绕过去。
客户等级同理（导入那条直接落库）。

单测证明不了这件事：`ensure_dictionary_value` 自己的单测当然过，
它测不到"某个入口忘了调它" —— 这个项目里已经验证过很多次，
**"忘接线"只能靠这种逐入口的真接口扫描**。

所以这套件的判据是：**同一个非法值，从每个入口试一遍，必须一律被拒**。
新增入口（比如以后的"从 ERP 同步产品"）时，把这里补上一段即可。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = os.getenv("API_BASE", "http://127.0.0.1:8000/api/v1")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _test_support  # noqa: E402

DB_NAME = _test_support.require_isolated_db()

from _db_helper import db  # noqa: E402

MARK = "CHKDICT"
#: 一个**一定不在字典里**的分类。别用 `包装` 那种"看起来像业务词"的值 ——
#: 万一以后有人把它加进字典，这套件就从"验拒绝"悄悄变成"验通过"了。
BAD_CATEGORY = "CHKDICT-不存在的分类"
BAD_LEVEL = "CHKDICT-不存在的等级"

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, actual, expected) -> None:
    ok = actual == expected
    RESULTS.append((label, ok, f"{actual!r}（期望 {expected!r}）"))
    print(f"  {'OK  ' if ok else 'FAIL'} {label}: {actual!r}（期望 {expected!r}）")


def check_true(label: str, value, detail: str = "") -> None:
    RESULTS.append((label, bool(value), detail))
    print(f"  {'OK  ' if value else 'FAIL'} {label}{('：' + detail) if detail else ''}")


def call(method: str, path: str, token: str | None = None, body=None, files=None):
    data = None
    headers: dict[str, str] = {}
    if files is not None:
        boundary = "----CHKDICT"
        parts = []
        for name, (filename, content) in files.items():
            parts.append(f"--{boundary}\r\n")
            parts.append(
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            )
            parts.append("Content-Type: text/csv\r\n\r\n")
            parts.append(content)
            parts.append("\r\n")
        parts.append(f"--{boundary}--\r\n")
        data = "".join(parts).encode("utf-8")
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(API + path, data=data, method=method)
    for key, value in headers.items():
        req.add_header(key, value)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode() or "{}")
        except Exception:
            return exc.code, {}


def csv_upload(text: str):
    return {"file": ("import.csv", text)}


def cleanup() -> None:
    """按外键顺序清干净，清不掉就直接报错（别把残留留给守门套件）。"""
    for sql in (
        f"delete from product_costs where sku_id in (select id from skus where sku_code like '{MARK}%')",
        f"delete from price_rules where sku_id in (select id from skus where sku_code like '{MARK}%')",
        f"delete from skus where sku_code like '{MARK}%'",
        f"delete from products where name like '{MARK}%'",
        f"delete from contacts where customer_id in (select id from customers where name like '{MARK}%')",
        f"delete from customers where name like '{MARK}%'",
    ):
        out = db(sql)
        if str(out).startswith("SQLERR"):
            raise AssertionError(f"清理失败（{sql[:70]}…）：{out[:160]}")


def main() -> int:
    status, res = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})
    admin = (res.get("data") or {}).get("access_token")
    if not admin:
        print("  登录失败:", res)
        return 1
    cleanup()

    # 字典里到底有什么，先摸清楚 —— 别假设，直接读库
    enabled_categories = [
        r for r in str(db(
            "select code from dictionary_items where type='product_category' and enabled order by sort_no"
        )).split("\n") if r
    ]
    print(f"=== 0. 当前启用的产品分类：{enabled_categories} ===")
    if not enabled_categories:
        print("  ✗ 字典里一个启用的产品分类都没有，后面的'合法值放行'验不了")
        return 1
    good_category = enabled_categories[0]

    print("\n=== 1. 单个新建：合法值放行、非法值拒绝 ===")
    status, res = call("POST", "/products", admin,
                       {"name": f"{MARK}-正常产品", "product_line": "x", "category": good_category})
    check("合法分类 → 放行", (status, res.get("code")), (200, 0))
    status, res = call("POST", "/products", admin,
                       {"name": f"{MARK}-非法产品", "product_line": "x", "category": BAD_CATEGORY})
    check_true("非法分类 → 被拒（不是静默写进去）", res.get("code") != 0,
               f"HTTP {status} code={res.get('code')} {str(res.get('message'))[:60]}")
    check("非法分类没有落库",
          db(f"select count(*) from products where name='{MARK}-非法产品'"), "0")

    print("\n=== 2. 编辑：把分类改成字典外的值也必须拒绝 ===")
    pid = db(f"select id from products where name='{MARK}-正常产品' order by id desc limit 1")
    status, res = call("PATCH", f"/products/{pid}", admin, {"category": BAD_CATEGORY})
    check_true("编辑成非法分类 → 被拒", res.get("code") != 0,
               f"HTTP {status} code={res.get('code')} {str(res.get('message'))[:60]}")
    check("库里分类没被改脏", db(f"select category from products where id={pid}"), good_category)
    # 只改别的字段（不带 category）不能被字典挡住 —— 否则等于把编辑功能锁死
    status, res = call("PATCH", f"/products/{pid}", admin, {"description": "只改描述"})
    check("不带分类的编辑照常放行", (status, res.get("code")), (200, 0))

    print("\n=== 3. 批量导入：同一把尺子（这是修之前漏掉的那条路）===")
    csv_text = "产品名称,分类\n"
    csv_text += f"{MARK}-导入合法,{good_category}\n"
    csv_text += f"{MARK}-导入非法,{BAD_CATEGORY}\n"
    status, res = call("POST", "/products/import", admin, files=csv_upload(csv_text))
    data = res.get("data") or {}
    check("导入请求本身成功", (status, res.get("code")), (200, 0))
    check("合法行落库", data.get("created_count"), 1)
    # ⚠️ 字段要写准：字典校验失败算 **`skipped_count`**（跳过），不是 `failed_count`。
    # 我第一版断言错了字段，红了一条其实功能正常的用例 ——
    # 所以这里两个都看：**总数对得上**（合法 1 + 非 0 的坏行 1），
    # 坏行必须被明确计数，不能既不落库也不计数（静默丢行）。
    check("坏行被计数（跳过）", data.get("skipped_count"), 1)
    check("坏行没被算成失败行（它是数据问题，不是程序错）", data.get("failed_count"), 0)
    check("总行数对得上（没有静默丢行）",
          (data.get("created_count") or 0) + (data.get("skipped_count") or 0)
          + (data.get("failed_count") or 0), 2)
    check("非法行确实没落库",
          db(f"select count(*) from products where name='{MARK}-导入非法'"), "0")
    check("合法行确实落库了",
          db(f"select count(*) from products where name='{MARK}-导入合法'"), "1")

    print("\n=== 4. 客户等级：单个入口 ===")
    status, res = call("POST", "/customers", admin, {"name": f"{MARK}-非法等级客户", "level": BAD_LEVEL})
    check_true("非法等级 → 被拒", res.get("code") != 0,
               f"HTTP {status} code={res.get('code')} {str(res.get('message'))[:60]}")
    check("非法等级没有落库",
          db(f"select count(*) from customers where name='{MARK}-非法等级客户'"), "0")
    status, res = call("POST", "/customers", admin, {"name": f"{MARK}-正常客户", "level": "C"})
    check("合法等级（C）→ 放行", (status, res.get("code")), (200, 0))

    print("\n=== 5. 客户等级：批量导入（修之前也漏了）===")
    csv_text = "客户名称,客户等级\n"
    csv_text += f"{MARK}-导入客户非法,{BAD_LEVEL}\n"
    csv_text += f"{MARK}-导入客户合法,C\n"
    status, res = call("POST", "/customers/import", admin, files=csv_upload(csv_text))
    data = res.get("data") or {}
    check("导入请求本身成功", (status, res.get("code")), (200, 0))
    check("非法等级行被计数（失败行：并进 errs 走的是 failed_row）",
          data.get("failed_count"), 1)
    check("非法等级没有落库",
          db(f"select count(*) from customers where name='{MARK}-导入客户非法'"), "0")
    check("合法等级行落库",
          db(f"select count(*) from customers where name='{MARK}-导入客户合法'"), "1")

    print("\n=== 6. 停用的字典项：老值能留、新写入必须拒（管理员停用就得真停用）===")
    db(f"update dictionary_items set enabled=false where type='product_category' and code='{good_category}'")
    try:
        status, res = call("POST", "/products", admin,
                           {"name": f"{MARK}-停用后新建", "product_line": "x", "category": good_category})
        check_true("停用后再新建该分类 → 被拒", res.get("code") != 0,
                   f"HTTP {status} code={res.get('code')} {str(res.get('message'))[:60]}")
        check("停用后确实没落库",
              db(f"select count(*) from products where name='{MARK}-停用后新建'"), "0")
    finally:
        db(f"update dictionary_items set enabled=true where type='product_category' and code='{good_category}'")

    cleanup()
    failed = [r for r in RESULTS if not r[1]]
    print()
    if failed:
        print(f"发现 {len(failed)} 项不符合预期：")
        for label, _ok, detail in failed:
            print(f"  - {label}：{detail}")
        return 1
    print(f"全部通过（{len(RESULTS)} 条）：受控字典在每个入口上都生效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
