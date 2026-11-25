#!/usr/bin/env python3
"""一次性真库回归：产品分类自由填写（2026-10-15用户确认），客户等级仍受控。

产品新建、编辑、导入接受字典外分类并拒绝超长；客户等级不能因产品口径变更而放宽。
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
CUSTOM_CATEGORY = "CHKDICT-用户自定义分类"
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

    print("=== 产品分类：新建、编辑、导入均支持自由填写 ===")
    status, res = call("POST", "/products", admin,
                       {"name": f"{MARK}-正常产品", "category": CUSTOM_CATEGORY})
    check("自定义分类新建", (status, res.get("code")), (200, 0))
    pid = (res.get("data") or {}).get("id")
    assert pid
    check("自定义分类原样保存", db(f"select category from products where id={pid}"), CUSTOM_CATEGORY)
    category64 = "类" * 64
    status, res = call("PATCH", f"/products/{pid}", admin, {"category": category64})
    check("编辑64字分类放行", (status, res.get("code")), (200, 0))
    check("64字分类原样保存", db(f"select category from products where id={pid}"), category64)
    status, res = call("PATCH", f"/products/{pid}", admin, {"category": "类" * 65})
    check("编辑65字分类校验拒绝", status, 400)
    check("超长编辑未改原值", db(f"select category from products where id={pid}"), category64)
    status, res = call("POST", "/products", admin, {"name": f"{MARK}-超长产品", "category": "类" * 65})
    check("新建65字分类校验拒绝", status, 400)
    status, res = call("PATCH", f"/products/{pid}", admin, {"category": None})
    check("分类允许清空", (status, res.get("code")), (200, 0))
    csv_text = "产品名称,分类\n" + f"{MARK}-导入自由,{CUSTOM_CATEGORY}\n" + f"{MARK}-导入超长,{chr(31867) * 65}\n"
    status, res = call("POST", "/products/import", admin, files=csv_upload(csv_text))
    data = res.get("data") or {}
    check("导入请求成功", (status, res.get("code")), (200, 0))
    check("自由分类导入成功", data.get("created_count"), 1)
    check("超长分类导入明确失败", data.get("failed_count"), 1)
    check("导入自定义值原样保存", db(f"select category from products where name='{MARK}-导入自由'"), CUSTOM_CATEGORY)
    check("超长导入未落库", db(f"select count(*) from products where name='{MARK}-导入超长'"), "0")

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

    cleanup()
    failed = [r for r in RESULTS if not r[1]]
    print()
    if failed:
        print(f"发现 {len(failed)} 项不符合预期：")
        for label, _ok, detail in failed:
            print(f"  - {label}：{detail}")
        return 1
    print(f"全部通过（{len(RESULTS)} 条）：产品分类自由填写，客户等级仍受控")
    return 0


if __name__ == "__main__":
    sys.exit(main())
