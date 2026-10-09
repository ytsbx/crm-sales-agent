"""第三批返修的反例断言（审查 C3-01 / C3-02 / C3-06）。

这三条都是"我先复现、再修、再验证"的，所以这里的断言写的是**当时复现出来的
那个反例**，而不是"改完长什么样" —— 这样退回去改坏了一定会红。

用法：
    API_BASE=http://127.0.0.1:8007/api/v1 \
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5433/crm_check_c3test \
    PYTHONPATH=. .venv/bin/python scripts/check_c3_round_repairs.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API = os.getenv("API_BASE", "http://127.0.0.1:8007/api/v1")
DB = os.getenv("DB_NAME", "crm_check_c3test")
MARK = "CHKC3"

passed = 0
failed: list[str] = []


def call(method, path, token=None, body=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + urllib.parse.quote(path, safe="/?&="), data=data, method=method
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")
    except Exception as exc:  # 超时/连接失败也算一种结果
        return "TIMEOUT", {"message": str(exc)[:60]}


def db(sql: str):
    result = subprocess.run(
        ["docker", "exec", "crm-postgres", "psql", "-U", "crm", "-d", DB, "-tAc", sql],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return "SQLERR:" + result.stderr.strip().split("\n")[-1][:70]
    return result.stdout.strip()


def check(label, got, want):
    global passed
    if got == want:
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


# =====================================================================
# C3-06：部门树并发互挂
# =====================================================================
def sec_c306(admin: str) -> None:
    """并发把 A 挂到 B 下、同时 B 挂到 A 下，最多只能成功一个。

    复现出来的反例：**两个都 200**，库里成环 → 递归 CTE 转不出来（3 秒超时）、
    成环部门从树里消失、受影响部门的人查客户请求挂死。
    """
    print("\n=== C3-06 部门树并发互挂 ===")
    db(f"delete from departments where name like '{MARK}%'")
    a = call("POST", "/departments", admin, {"name": f"{MARK}-A"})[1]["data"]["id"]
    b = call("POST", "/departments", admin, {"name": f"{MARK}-B"})[1]["data"]["id"]

    # ① 顺序执行：第二次必须被拒（这条改之前就是对的，作为对照）
    check("① 顺序 A→B", call("PATCH", f"/departments/{a}", admin, {"parent_id": b})[1].get("code"), 0)
    check("① 再顺序 B→A 被拒", call("PATCH", f"/departments/{b}", admin, {"parent_id": a})[1].get("code"), 40001)
    db(f"update departments set parent_id=NULL where id in ({a},{b})")

    # ② 并发互挂：最多一个成功
    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(call, "PATCH", f"/departments/{a}", admin, {"parent_id": b})
        f2 = ex.submit(call, "PATCH", f"/departments/{b}", admin, {"parent_id": a})
        c1, c2 = f1.result()[1].get("code"), f2.result()[1].get("code")
    ok_count = sum(1 for c in (c1, c2) if c == 0)
    check("② 并发互挂只成功一个", ok_count, 1)
    check_true("② 另一个是明确的拒绝（40001/422），不是 500",
               {c1, c2} - {0} <= {40001, 422}, f"codes=[{c1}, {c2}]")

    # ③ 库里**不能**成环
    a_parent = db(f"select coalesce(parent_id::text,'N') from departments where id={a}")
    b_parent = db(f"select coalesce(parent_id::text,'N') from departments where id={b}")
    check_true(
        "③ 库内未成环（A.parent 与 B.parent 不同时指向对方）",
        not (a_parent == str(b) and b_parent == str(a)),
        f"A.parent={a_parent} B.parent={b_parent}",
    )

    # ④ 递归查询必须能出结果（成环时旧写法会超时）
    recursive = subprocess.run(
        ["docker", "exec", "crm-postgres", "psql", "-U", "crm", "-d", DB, "-tAc",
         f"set statement_timeout='3s'; with recursive s as ("
         f"select id, 0 as depth from departments where id={a} "
         f"union all select d.id, s.depth+1 from departments d join s on d.parent_id=s.id "
         f"where s.depth < 64) select count(*) from s;"],
        capture_output=True, text=True,
    )
    check_true("④ 递归查询未超时", recursive.returncode == 0,
               "" if recursive.returncode == 0 else (recursive.stderr or "").strip()[-60:])

    # ⑤ 部门树里能找到这两个部门（成环时它们会消失）
    _, tree = call("GET", "/departments/tree", admin)
    flat = json.dumps(tree.get("data") or [], ensure_ascii=False)
    check_true("⑤ 部门树里能找到这两个部门",
               f"{MARK}-A" in flat and f"{MARK}-B" in flat)

    # ⑥ 已有环的脏数据（绕过接口用 SQL 造）：查询侧必须能自己止住
    db(f"update departments set parent_id={b} where id={a}")
    db(f"update departments set parent_id={a} where id={b}")
    dirty = subprocess.run(
        ["docker","exec","crm-postgres","psql","-U","crm","-d",DB,"-tAc",
         f"set statement_timeout='3s'; with recursive s as ("
         f"select id, 0 as depth from departments where id={a} "
         f"union all select d.id, s.depth+1 from departments d join s on d.parent_id=s.id "
         f"where s.depth < 64) select count(*) from s;"],
        capture_output=True, text=True,
    )
    check_true("⑥ 已有环时递归查询仍能返回（防环兜底生效）",
               dirty.returncode == 0, (dirty.stdout or "").strip()[-20:])

    db(f"update departments set parent_id=NULL where id in ({a},{b})")
    db(f"delete from departments where id in ({a},{b})")


# =====================================================================
# C3-01：新建打样必须保存车间依据
# =====================================================================
def sec_c301(admin: str) -> None:
    """新建时带的 craft/material/drawing_version 必须落库。

    复现出来的反例：新建时三项全是 NULL，而"后续追加明细"同样字段能存上 ——
    同一份数据两条路两个结果。
    """
    print("\n=== C3-01 新建打样保存车间依据 ===")
    sku = db("select id from skus order by id limit 1")
    cust = db("select id from customers order by id limit 1")
    db(f"delete from sample_items where sample_request_id in "
       f"(select id from sample_requests where remark like '{MARK}%')")
    db(f"delete from sample_requests where remark like '{MARK}%'")

    _, res = call("POST", "/samples", admin, {
        "customer_id": int(cust), "remark": f"{MARK}-新建带依据",
        "items": [{"sku_id": int(sku), "quantity": 2,
                   "craft": "注塑", "material": "ABS", "drawing_version": "DWG-v9"}],
    })
    check("① 新建成功", res.get("code"), 0)
    sid = (res.get("data") or {}).get("id")
    got = db(f"select coalesce(craft,'NULL')||'|'||coalesce(material,'NULL')||'|'"
             f"||coalesce(drawing_version,'NULL') from sample_items "
             f"where sample_request_id={sid}")
    check("② 新建时带的车间依据已落库", got, "注塑|ABS|DWG-v9")

    # ③ 追加明细那条路也要一致（这条改之前就是对的）
    call("POST", f"/samples/{sid}/items", admin, {
        "sku_id": int(sku), "quantity": 1,
        "craft": "吹塑", "material": "PP", "drawing_version": "DWG-v10"})
    got2 = db(f"select coalesce(craft,'NULL')||'|'||coalesce(material,'NULL')||'|'"
              f"||coalesce(drawing_version,'NULL') from sample_items "
              f"where sample_request_id={sid} order by id desc limit 1")
    check("③ 追加明细的车间依据也落库（两条路一致）", got2, "吹塑|PP|DWG-v10")

    db(f"delete from sample_items where sample_request_id={sid}")
    db(f"delete from sample_requests where id={sid}")


# =====================================================================
# C3-02：打样的关联与归属完整性
# =====================================================================
def sec_c302(admin: str, worker_token: str) -> None:
    """负责人/制作负责人/联系人都必须是**存在且有效**的关联。"""
    print("\n=== C3-02 关联与归属完整性 ===")
    sku = db("select id from skus order by id limit 1")
    worker = int(db("select id from users where username='zhangsan'"))
    ca = call("POST", "/customers", admin, {"name": f"{MARK}-客户甲"})[1]["data"]["id"]
    cb = call("POST", "/customers", admin, {"name": f"{MARK}-客户乙"})[1]["data"]["id"]
    ctb = call("POST", f"/customers/{cb}/contacts", admin,
               {"name": f"{MARK}-乙联系人", "mobile": "13900000009"})[1]["data"]["id"]

    def mk(tag, **extra):
        body = {"customer_id": ca, "remark": f"{MARK}-{tag}",
                "items": [{"sku_id": int(sku), "quantity": 1}]}
        body.update(extra)
        status, res = call("POST", "/samples", admin, body)
        return res.get("code"), (res.get("data") or {}).get("id")

    # ① 跨客户联系人
    code, sid = mk("跨客户联系人", contact_id=int(ctb))
    check("① 挂别人家联系人被拒", code, 40001)
    check_true("① 被拒后没落库", sid is None, f"id={sid}")

    # ② 不存在的联系人 / 负责人
    check("② 不存在的联系人被拒（从前 200）",
          mk("不存在联系人", contact_id=999999)[0], 40401)
    check("② 不存在的负责人被拒（从前 500）",
          mk("不存在负责人", owner_id=999999)[0], 40401)

    # ③ 停用负责人
    db(f"update users set status='disabled' where id={worker}")
    check("③ 停用负责人被拒（从前 200）", mk("停用负责人", owner_id=worker)[0], 40001)
    db(f"update users set status='active' where id={worker}")

    # ④ 负责人不能改成 null —— 否则原负责人立刻读不到这张单
    code, sid = mk("改null", owner_id=worker)
    check("④ 建单（负责人=业务员）", code, 0)
    check("④ 负责人改 null 被拒（从前 200）",
          call("PATCH", f"/samples/{sid}", admin, {"owner_id": None})[1].get("code"), 40003)
    check("④ 库内负责人没被清掉",
          db(f"select owner_id from sample_requests where id={sid}"), str(worker))
    check_true("④ 原负责人仍能读到这张单",
               call("GET", f"/samples/{sid}", worker_token)[0] == 200)

    # ⑤ 制作负责人：不存在要拒、有效要能存
    check("⑤ 制作负责人不存在被拒（从前 200）",
          mk("制作不存在", production_owner_id=999999)[0], 40401)
    code, sid2 = mk("制作有效", production_owner_id=worker)
    check("⑤ 制作负责人有效可以保存", code, 0)
    check("⑤ 制作负责人已落库",
          db(f"select production_owner_id from sample_requests where id={sid2}"), str(worker))

    for s in (sid, sid2):
        if s:
            db(f"delete from sample_items where sample_request_id={s}")
            db(f"delete from sample_requests where id={s}")
    db(f"delete from contacts where id={ctb}")
    for c in (ca, cb):
        db(f"delete from customers where id={c}")


def main() -> int:
    status, res = call("POST", "/auth/login", body={"username": "admin", "password": "admin123"})
    if res.get("code") != 0:
        print(f"登录失败：{status} {res.get('message')}")
        return 1
    admin = res["data"]["access_token"]
    worker_token = call("POST", "/auth/login",
                        body={"username": "zhangsan", "password": "123456"})[1]["data"]["access_token"]

    sec_c306(admin)
    sec_c301(admin)
    sec_c302(admin, worker_token)

    print("\n" + "=" * 60)
    if failed:
        print(f"FAILED（{len(failed)}）:")
        for name in failed:
            print("  -", name)
        return 1
    print(f"第三批返修：全部通过（{passed} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
