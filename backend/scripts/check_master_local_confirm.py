"""本地直接确认主数据（issue：正式报价流程阻断）的反例与正向断言。

复现的链条（审查 2026-10-10 实测）：

    本地建 SKU → 不产生确认记录、不产生快照
              → 唯一的确认动作挂在**差异记录**上，而本地 SKU 零差异
              → 没有可引用的已确认版本 → **正式发送被硬拦且无路可走**

断言写的是**当时复现出来的那个状态**，不是"改完长什么样"。

跑法：
    API_BASE=http://127.0.0.1:8000/api/v1 \
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_check_test_mc \
    PYTHONPATH=. .venv/bin/python scripts/check_master_local_confirm.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = os.getenv("API_BASE", "http://127.0.0.1:8000/api/v1")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _test_support import require_isolated_db  # noqa: E402

require_isolated_db()

from sqlalchemy import text  # noqa: E402

passed = 0
failed: list[str] = []
MARK = "CHKMC"
QUOTE_FIELDS = ("name", "specification", "unit")


def check(label, got, want):
    global passed
    if str(got) == str(want):
        passed += 1
        print(f"  OK   {label}: {got!r}")
    else:
        failed.append(label)
        print(f"  FAIL {label}: {got!r}（期望 {want!r}）")


def check_true(label, ok, detail=""):
    global passed
    if ok:
        passed += 1
        print(f"  OK   {label} {detail}")
    else:
        failed.append(label)
        print(f"  FAIL {label} {detail}")


def call(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + urllib.parse.quote(path, safe="/?&=%"), data=data, method=method
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


async def _scalar(sql: str) -> str:
    from app.core.database import SessionLocal

    async with SessionLocal() as s:
        return str((await s.execute(text(sql))).scalar_one())


async def _confirmed_version(sku_id: int) -> int:
    return int(
        await _scalar(
            f"select count(*) from sku_field_authorities "
            f"where sku_id={sku_id} and confirmed_version > 0"
        )
    )


async def _snapshot_count(sku_id: int) -> int:
    return int(
        await _scalar(f"select count(*) from sku_master_versions where sku_id={sku_id}")
    )


async def _gate(sku_id: int):
    """直接调真实业务闸门。返回 (是否放行, 说明)。"""
    from app.core.database import SessionLocal
    from app.modules.product import master as M

    async with SessionLocal() as s:
        try:
            out = await M.require_confirmed_master(s, sku_id, list(QUOTE_FIELDS))
            return True, out["message"]
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)


async def cleanup() -> None:
    from app.core.database import SessionLocal

    async with SessionLocal() as s:
        for sql in (
            f"delete from sku_field_authorities where sku_id in "
            f"(select id from skus where sku_code like '{MARK}%')",
            f"delete from sku_master_versions where sku_id in "
            f"(select id from skus where sku_code like '{MARK}%')",
            f"delete from skus where sku_code like '{MARK}%'",
            f"delete from products where name like '{MARK}%'",
        ):
            await s.execute(text(sql))
        await s.commit()


async def main() -> int:
    status, res = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})
    admin = (res.get("data") or {}).get("access_token")
    if not admin:
        print("  管理员登录失败:", res)
        return 1
    status, res = call("POST", "/auth/login", body={"username": "zhangsan", "password": "123456"})
    zs = (res.get("data") or {}).get("access_token")
    if not zs:
        print("  业务员登录失败:", res)
        return 1

    try:
        await cleanup()
        print("\n=== 本地建一个 SKU（复现链条的起点）===")
        call("POST", "/products", admin, {"name": f"{MARK}-产品", "product_line": "x", "category": "包装"})
        status, res = call("GET", f"/products?keyword={MARK}-产品&page=1&page_size=5", admin)
        pid = ((res.get("data") or {}).get("items") or [{}])[0].get("id")
        status, res = call(
            "POST", f"/products/{pid}/skus", admin,
            {"sku_code": f"{MARK}-001", "name": "本地件", "specification": "100×200", "unit": "个"},
        )
        check_true("建 SKU 成功", res.get("code") == 0, f"code={res.get('code')}")
        status, res = call("GET", f"/skus?keyword={MARK}-001&page=1&page_size=5", admin)
        sku = ((res.get("data") or {}).get("items") or [{}])[0].get("id")
        check_true("拿得到 sku_id", bool(sku), f"sku={sku}")

        print("\n=== ① 复现：本地建 SKU 后零确认、零快照、零差异 ===")
        check("确认记录数", await _confirmed_version(sku), 0)
        check("主数据快照版本数", await _snapshot_count(sku), 0)
        status, res = call("GET", "/sku-master/diffs?page=1&page_size=50", admin)
        diffs = (res.get("data") or {}).get("items") or []
        mine = [d for d in diffs if d.get("sku_id") == sku]
        check("这个 SKU 的差异条数（没有差异 = 没有核定入口）", len(mine), 0)

        print("\n=== ② 复现：闸门拦着，且原本无路可走 ===")
        allowed, why = await _gate(sku)
        check_true("require_confirmed_master 拦住", not allowed, why[:76])

        print("\n=== ③ 权限：无 product:manage 者不能确认 ===")
        status, res = call("POST", f"/sku-master/skus/{sku}/confirm-local", zs, {"sku_id": sku})
        check_true("业务员被拒", res.get("code") != 0, f"code={res.get('code')}")
        check("  且没有写入确认记录", await _confirmed_version(sku), 0)

        print("\n=== ④ 本地直接确认（三个字段一起）===")
        status, res = call(
            "POST", f"/sku-master/skus/{sku}/confirm-local", admin,
            {"sku_id": sku, "note": "套件：本地核对无误"},
        )
        d = res.get("data") or {}
        check_true("确认成功", res.get("code") == 0, f"code={res.get('code')}")
        check("确认的字段", sorted(d.get("confirmed_fields") or []), sorted(QUOTE_FIELDS))
        check_true("生成了快照", bool(d.get("version_no")), f"v{d.get('version_no')}")
        check("库内确认记录数", await _confirmed_version(sku), 3)
        check("库内快照版本数", await _snapshot_count(sku), 1)
        check_true("记下了操作人", d.get("confirmed_by") is not None, str(d.get("confirmed_by")))
        check_true("记下了时间", bool(d.get("confirmed_at")), str(d.get("confirmed_at"))[:19])
        check_true(
            "确认的是当前本地值",
            (d.get("values") or {}).get("specification") == "100×200",
            str(d.get("values")),
        )

        print("\n=== ⑤ 确认后闸门放行 ===")
        allowed, why = await _gate(sku)
        check_true("require_confirmed_master 放行", allowed, why[:70])

        from app.core.database import SessionLocal
        from app.modules.product import master as M

        async with SessionLocal() as s:
            probs = await M.quoted_snapshot_problems(
                s, sku_id=sku, version_no=d.get("version_no")
            )
        check("所引用版本对客字段齐全（无 problem）", probs, [])

        print("\n=== ⑥ 幂等：值没变不新增版本 ===")
        before = await _snapshot_count(sku)
        status, res = call("POST", f"/sku-master/skus/{sku}/confirm-local", admin, {"sku_id": sku})
        d2 = res.get("data") or {}
        check_true("再次确认成功但没建新快照", d2.get("snapshot_created") is False,
                   f"snapshot_created={d2.get('snapshot_created')}")
        check("快照版本数不变", await _snapshot_count(sku), before)

        print("\n=== ⑦ 值改了才出新版本 ===")
        call("PATCH", f"/skus/{sku}", admin, {"specification": "200×300"})
        status, res = call("POST", f"/sku-master/skus/{sku}/confirm-local", admin, {"sku_id": sku})
        d3 = res.get("data") or {}
        check_true("建了新快照", d3.get("snapshot_created") is True,
                   f"v{d3.get('version_no')}")
        check("改动的字段", d3.get("changed_labels"), ["规格"])
        check("快照版本数", await _snapshot_count(sku), before + 1)
        check_true(
            "新快照里是新值",
            (d3.get("values") or {}).get("specification") == "200×300",
            str(d3.get("values")),
        )

        print("\n=== ⑧ 空规格是合法的已确认值（不能当成缺字段）===")
        call("POST", "/products", admin, {"name": f"{MARK}-无规格", "product_line": "x", "category": "包装"})
        status, res = call("GET", f"/products?keyword={MARK}-无规格&page=1&page_size=5", admin)
        pid2 = ((res.get("data") or {}).get("items") or [{}])[0].get("id")
        call("POST", f"/products/{pid2}/skus", admin,
             {"sku_code": f"{MARK}-002", "name": "无规格件", "unit": "个"})
        status, res = call("GET", f"/skus?keyword={MARK}-002&page=1&page_size=5", admin)
        sku2 = ((res.get("data") or {}).get("items") or [{}])[0].get("id")
        status, res = call("POST", f"/sku-master/skus/{sku2}/confirm-local", admin, {"sku_id": sku2})
        d4 = res.get("data") or {}
        check_true("规格为空的 SKU 也能确认", res.get("code") == 0, f"code={res.get('code')}")
        allowed2, why2 = await _gate(sku2)
        check_true("且确认后闸门放行（空规格不被当成缺字段）", allowed2, why2[:70])
        check_true("快照里带着 specification 这个键",
                   "specification" in (d4.get("values") or {}),
                   str(d4.get("values")))

        print("\n=== ⑨ 路径与请求体的 sku_id 不一致要拒 ===")
        status, res = call("POST", f"/sku-master/skus/{sku}/confirm-local", admin,
                           {"sku_id": 999999})
        check_true("不一致被拒", res.get("code") != 0, f"code={res.get('code')}")
    finally:
        await cleanup()

    print()
    print("=" * 60)
    if failed:
        print(f"FAILED（{len(failed)}）: {failed}")
        return 1
    print(f"本地直接确认主数据：全部通过（{passed} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
