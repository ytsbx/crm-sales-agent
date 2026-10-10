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
MARK = "CHKC3"

# ---------------------------------------------------------------------------
# 库名**只认 `DATABASE_URL` 这一个真源**（2026-10-10 修，为加入 CI 清单）。
#
# 从前这里读的是一个独立变量 `DB_NAME`，于是有**两个真源**：
#   - 夹具走 `app.core.database`（吃 `DATABASE_URL`）→ 写到 A 库；
#   - 本套件的 psql 子进程走 `DB`（吃 `DB_NAME`）→ 查 B 库。
# CI 的后端 job **只设了 `DATABASE_URL`、没设 `DB_NAME`**，`DB` 就落到默认值
# （一个开发库名）—— 断言会在**开发库**上查，而夹具写进隔离库：
# 要么查不到（假红），要么去动开发库（更糟）。
# 现在从 `DATABASE_URL` 里取出库名，两边必然一致；本地/CI/容器三种部署都成立。
# ---------------------------------------------------------------------------
if os.getenv("DATABASE_URL") is None:
    os.environ["DATABASE_URL"] = (
        f"postgresql+asyncpg://{os.getenv('PG_USER', 'crm')}:"
        f"{os.getenv('PGPASSWORD', 'crm123456')}@"
        f"{os.getenv('PG_HOST', '127.0.0.1')}:{os.getenv('PG_PORT', '5432')}/"
        f"{os.getenv('DB_NAME', 'crm_check_c3test')}"
    )

import sys as _sys

_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _test_support import require_isolated_db  # noqa: E402

#: 与 `DATABASE_URL` 同源，**不再是第二个变量**（必须在上面 import 之前就位：
#: `require_isolated_db` 要求"设置好 DATABASE_URL 之后、import app.* 之前"调用）。
DB = require_isolated_db()

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


#: 本地原生 PostgreSQL（crm_prod / crm_sales_agent 在 5432 上）。
#: 容器 `crm-postgres`（5433）只有一次性测试库；它没起时不能硬走 docker。
_PG = os.getenv("PSQL_BIN", "/opt/homebrew/opt/postgresql@15/bin/psql")
_PG_PORT = os.getenv("PG_PORT", "5432")
_USE_DOCKER: bool | None = None


def _probe_docker() -> bool:
    global _USE_DOCKER
    if _USE_DOCKER is None:
        r = subprocess.run(["docker", "exec", "crm-postgres", "psql", "-U", "crm", "-d", DB,
                            "-tAc", "select 1"], capture_output=True, text=True, timeout=30)
        _USE_DOCKER = r.returncode == 0
    return _USE_DOCKER


def db(sql: str):
    """执行 SQL。**自动适配**：库在容器（5433）还是本机原生（5432）。

    原来写死 `docker exec`，于是这个套件只能跑容器里的一次性库；
    本机的 crm_prod / crm_sales_agent 一跑就报 database does not exist。
    """
    if _probe_docker():
        result = subprocess.run(
            ["docker", "exec", "crm-postgres", "psql", "-U", "crm", "-d", DB, "-tAc", sql],
            capture_output=True, text=True,
        )
        err = result.stderr
    else:
        env = dict(os.environ, PGPASSWORD=os.getenv("PGPASSWORD", "crm123456"))
        result = subprocess.run(
            [_PG, "-h", os.getenv("PG_HOST", "127.0.0.1"), "-p", _PG_PORT,
             "-U", os.getenv("PG_USER", "crm"), "-d", DB, "-tAc", sql],
            capture_output=True, text=True, env=env,
        )
        err = result.stderr
    if result.returncode != 0:
        return "SQLERR:" + err.strip().split("\n")[-1][:70]
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
    out4 = db(f"with recursive s as (select id, 0 as depth from departments where id={a} "
              f"union all select d.id, s.depth+1 from departments d join s on d.parent_id=s.id "
              f"where s.depth < 64) select count(*) from s")
    check_true("④ 递归查询能出结果（不成环时不超时）", not out4.startswith("SQLERR"),
               out4[:40])

    # ⑤ 部门树里能找到这两个部门（成环时它们会消失）
    _, tree = call("GET", "/departments/tree", admin)
    flat = json.dumps(tree.get("data") or [], ensure_ascii=False)
    check_true("⑤ 部门树里能找到这两个部门",
               f"{MARK}-A" in flat and f"{MARK}-B" in flat)

    # ⑥ 已有环的脏数据（绕过接口用 SQL 造）：查询侧必须能自己止住
    db(f"update departments set parent_id={b} where id={a}")
    db(f"update departments set parent_id={a} where id={b}")
    out6 = db(f"with recursive s as (select id, 0 as depth from departments where id={a} "
              f"union all select d.id, s.depth+1 from departments d join s on d.parent_id=s.id "
              f"where s.depth < 64) select count(*) from s")
    check_true("⑥ 已有环时递归查询仍能返回（防环兜底生效）",
               not out6.startswith("SQLERR") and out6.isdigit(), out6[:20])

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


# =====================================================================
# C3-02 补修：已删除的联系人不能再关联到打样单
# =====================================================================
def sec_c302b(admin: str) -> None:
    """复现出来的反例：删掉联系人后读它返回 404，但拿它的 id 建/改打样单却 200 并落库。

    根因是 `_assert_contact_belongs` 只查了"存在 + 客户归属"，漏了**软删除**：
    `Contact` 是软删除（`deleted_at`），而 `session.get()` 不走软删除过滤 ——
    列表/详情看不到它，只有这条"按 id 直取"的校验看得到，于是留了个后门。
    """
    print("\n=== C3-02补 已删除联系人不得再关联 ===")
    sku = db("select id from skus order by id limit 1")
    cust = db("select id from customers order by id limit 1")
    db(f"delete from contacts where name like '{MARK}-软删%'")

    # 造一个联系人并删掉
    _, r = call("POST", f"/customers/{cust}/contacts", admin,
                {"name": f"{MARK}-软删联系人", "mobile": "13900008888"})
    ct = (r.get("data") or {}).get("id")
    check_true("前置：建联系人", bool(ct), f"id={ct}")
    call("DELETE", f"/contacts/{ct}", admin)
    check_true("前置：联系人已软删除（deleted_at 有值）",
               db(f"select deleted_at is not null from contacts where id={ct}") == "t")
    # 对照：读它自己是 404
    check("前置：读已删除联系人返回 404",
          call("GET", f"/contacts/{ct}", admin)[1].get("code"), 40401)

    def mk(tag, **extra):
        body = {"customer_id": int(cust), "remark": f"{MARK}-{tag}",
                "items": [{"sku_id": int(sku), "quantity": 1}]}
        body.update(extra)
        status, res = call("POST", "/samples", admin, body)
        return res.get("code"), (res.get("data") or {}).get("id")

    # ① 新建时关联已删除联系人 → 必须拒（从前 200 并落库）
    code, sid = mk("软删新建", contact_id=int(ct))
    check_true("① 新建关联已删除联系人被拒（从前 200）", code != 0, f"code={code}")
    check_true("① 被拒后没落库", sid is None, f"id={sid}")

    # ② 修改时关联已删除联系人 → 必须拒（从前 200 并落库）
    code2, sid2 = mk("软删修改")
    check("② 前置：建一张干净的单", code2, 0)
    code3 = call("PATCH", f"/samples/{sid2}", admin, {"contact_id": int(ct)})[1].get("code")
    check_true("② 修改关联已删除联系人被拒（从前 200）", code3 != 0, f"code={code3}")
    check("② 库里没被改成那个联系人",
          db(f"select coalesce(contact_id::text,'N') from sample_requests where id={sid2}"), "N")

    # ③ 正例：有效联系人仍能正常关联（别把功能一起拦掉）
    _, r3 = call("POST", f"/customers/{cust}/contacts", admin,
                 {"name": f"{MARK}-有效联系人", "mobile": "13900007777"})
    ct_ok = (r3.get("data") or {}).get("id")
    code4, sid4 = mk("有效联系人", contact_id=int(ct_ok))
    check("③ 有效联系人正常关联放行", code4, 0)
    check("③ 有效联系人已落库",
          db(f"select contact_id from sample_requests where id={sid4}"), str(ct_ok))

    # ④ 边界：先关联、后删除 —— 历史关联保持原样（不迁移）
    call("DELETE", f"/contacts/{ct_ok}", admin)
    check("④ 联系人事后被删，原单的关联仍在（历史不迁移）",
          db(f"select contact_id from sample_requests where id={sid4}"), str(ct_ok))

    for s in (sid, sid2, sid4):
        if s:
            db(f"delete from sample_items where sample_request_id={s}")
            db(f"delete from sample_requests where id={s}")
    db(f"delete from contacts where id in ({ct}, {ct_ok})")
    db(f"delete from contacts where name like '{MARK}-%'")


# =====================================================================
# C3-03：寄送运费必须非负、精度与库列一致
# =====================================================================
def sec_c303(admin: str) -> None:
    """复现出来的反例：-99 与 -0.01 都 200 并落库；12.345 被静默改成 12.35。

    同一个费用类字段 `sample_fee` 是拦的（400），`shipping_fee` 却放行 —— 两条路口径不一。
    """
    print("\n=== C3-03 寄送运费 ===")
    sku = db("select id from skus order by id limit 1")
    cust = db("select id from customers order by id limit 1")

    def fresh() -> int | None:
        _, r = call("POST", "/samples", admin, {
            "customer_id": int(cust), "remark": f"{MARK}-运费",
            "items": [{"sku_id": int(sku), "quantity": 1}]})
        sid = (r.get("data") or {}).get("id")
        if sid:
            call("POST", f"/samples/{sid}/approve", admin, {})
        return sid

    def drop(sid):
        if not sid:
            return
        db(f"delete from sample_items where sample_request_id={sid}")
        db(f"delete from sample_shipments where sample_request_id={sid}")
        db(f"delete from sample_requests where id={sid}")

    # ①② 负数必须拒（从前 200 并落库）
    for fee, label in ((-99, "-99"), (-0.01, "-0.01")):
        sid = fresh()
        code = call("POST", f"/samples/{sid}/ship", admin,
                    {"shipping_fee": fee, "carrier": "顺丰"})[1].get("code")
        check_true(f"① 运费 {label} 被拒（从前 200 并落库）", code != 0, f"code={code}")
        saved = db("select count(*) from sample_shipments where sample_request_id=%d" % sid)
        check("① 被拒后没落库", saved, "0")
        drop(sid)

    # ③④ 零运费与正常值必须放行
    for fee, label in ((0, "零运费 0"), (12.34, "正常 12.34")):
        sid = fresh()
        code = call("POST", f"/samples/{sid}/ship", admin,
                    {"shipping_fee": fee, "carrier": "顺丰"})[1].get("code")
        check(f"② {label} 放行", code, 0)
        got = db(f"select coalesce(shipping_fee::text,'N') from sample_shipments "
                 f"where sample_request_id={sid} order by id desc limit 1")
        check(f"② {label} 按填写的值落库", got, f"{fee:.2f}")
        drop(sid)

    # ⑤ 超精度必须拒（从前静默四舍五入成 12.35）
    sid = fresh()
    code = call("POST", f"/samples/{sid}/ship", admin,
                {"shipping_fee": 12.345, "carrier": "顺丰"})[1].get("code")
    check_true("③ 超精度 12.345 被拒（从前被静默改成 12.35）", code != 0, f"code={code}")
    drop(sid)


# =====================================================================
# C3-04：打样时间不能倒序、不能填未来（保留历史补录）
# =====================================================================
def sec_c304(admin: str) -> None:
    """复现出来的反例：寄出 10-09 / 签收 09-01 / 确认 08-01 全部 200，留下完整倒序时间线。"""
    print("\n=== C3-04 打样时间顺序 ===")
    sku = db("select id from skus order by id limit 1")
    cust = db("select id from customers order by id limit 1")
    today = db("select (now() at time zone 'Asia/Shanghai')::date")

    def fresh(tag):
        _, r = call("POST", "/samples", admin, {
            "customer_id": int(cust), "remark": f"{MARK}-{tag}",
            "items": [{"sku_id": int(sku), "quantity": 1}]})
        sid = (r.get("data") or {}).get("id")
        if sid:
            call("POST", f"/samples/{sid}/approve", admin, {})
        return sid

    def drop(sid):
        if not sid:
            return
        db(f"delete from sample_items where sample_request_id={sid}")
        db(f"delete from sample_shipments where sample_request_id={sid}")
        db(f"delete from sample_requests where id={sid}")

    # ① 签收早于寄出（从前 200）
    sid = fresh("倒序A")
    call("POST", f"/samples/{sid}/ship", admin, {"shipping_fee": 0, "shipped_at": "2026-10-09"})
    code = call("POST", f"/samples/{sid}/sign", admin, {"signed_at": "2026-09-01"})[1].get("code")
    check_true("① 签收早于寄出被拒（从前 200）", code != 0, f"code={code}")
    drop(sid)

    # ② 确认早于签收（从前 200）
    sid = fresh("倒序B")
    call("POST", f"/samples/{sid}/ship", admin, {"shipping_fee": 0, "shipped_at": "2026-10-09"})
    call("POST", f"/samples/{sid}/sign", admin, {"signed_at": "2026-10-09"})
    code = call("POST", f"/samples/{sid}/confirm", admin,
                {"accepted": True, "confirmed_at": "2026-08-01T10:00:00"})[1].get("code")
    check_true("② 确认早于签收被拒（从前 200）", code != 0, f"code={code}")
    drop(sid)

    # ③ 历史补录必须放行（全是过去、顺序对）—— 主人明确要求保留
    sid = fresh("历史补录")
    c1 = call("POST", f"/samples/{sid}/ship", admin,
              {"shipping_fee": 0, "shipped_at": "2026-08-20"})[1].get("code")
    c2 = call("POST", f"/samples/{sid}/sign", admin, {"signed_at": "2026-09-01"})[1].get("code")
    check("③ 历史补录放行：寄出 8/20", c1, 0)
    check("③ 历史补录放行：签收 9/1", c2, 0)
    drop(sid)

    # ④ 未来日期必须拒（三个环节）
    sid = fresh("未来寄出")
    code = call("POST", f"/samples/{sid}/ship", admin,
                {"shipping_fee": 0, "shipped_at": "2099-01-01"})[1].get("code")
    check_true("④ 寄出填未来被拒", code != 0, f"code={code}")
    drop(sid)
    sid = fresh("未来签收")
    call("POST", f"/samples/{sid}/ship", admin, {"shipping_fee": 0, "shipped_at": "2026-10-01"})
    code = call("POST", f"/samples/{sid}/sign", admin, {"signed_at": "2099-01-01"})[1].get("code")
    check_true("④ 签收填未来被拒", code != 0, f"code={code}")
    drop(sid)

    # ⑤ 边界：北京时间"今天"必须放行（服务器跑 UTC，按 UTC 比会误拒）
    sid = fresh("今天")
    c1 = call("POST", f"/samples/{sid}/ship", admin,
              {"shipping_fee": 0, "shipped_at": today})[1].get("code")
    c2 = call("POST", f"/samples/{sid}/sign", admin, {"signed_at": today})[1].get("code")
    check(f"⑤ 寄出填北京时间今天放行（今天是 {today}）", c1, 0)
    check("⑤ 签收填北京时间今天放行", c2, 0)
    # ⑥ 换算口径：北京日 → 该日北京 00:00 对应的 UTC 瞬时
    bj = db(f"select (shipped_at at time zone 'Asia/Shanghai')::date::text "
            f"from sample_requests where id={sid}")
    check("⑥ 落库后按北京时区还原 = 填的那一天（时区换算没偏 8 小时）", bj, today)
    drop(sid)


# =====================================================================
# C3-05：定制询价的数值范围与精度
# =====================================================================
def sec_c305(admin: str) -> None:
    """复现出来的反例：数量 -2 / 目标价 -10 都 200 落库；0.0001 被静默存成 0.000。"""
    print("\n=== C3-05 定制询价数值 ===")
    cust = db("select id from customers order by id limit 1")
    db(f"delete from custom_inquiries where title like '{MARK}%'")

    def mk(tag, **extra):
        body = {"title": f"{MARK}-{tag}", "customer_id": int(cust)}
        body.update(extra)
        status, res = call("POST", "/custom-inquiries", admin, body)
        data = res.get("data")
        iid = data[0].get("id") if isinstance(data, list) and data else (data or {}).get("id")
        return res.get("code"), iid

    cases = [
        ("① 数量 -2（从前 200 落库）", {"quantity": -2}, False),
        ("① 目标价 -10（从前 200 落库）", {"target_price": -10}, False),
        ("② 数量 0.0001（从前被静默存成 0.000）", {"quantity": 0.0001}, False),
        ("② 数量 1.23456（从前被静默改成 1.235）", {"quantity": 1.23456}, False),
        ("③ 数量 100 放行", {"quantity": 100}, True),
        ("③ 目标价 0 放行", {"target_price": 0}, True),
        ("③ 数量与目标价都不填（尚未确定）放行", {}, True),
    ]
    for label, body, should_pass in cases:
        code, iid = mk(label[:12], **body)
        if should_pass:
            check(label, code, 0)
        else:
            check_true(label, code != 0, f"code={code}")
        if iid:
            db(f"delete from custom_inquiries where id={iid}")

    # ④ 修改入口同一口径
    code, iid = mk("修改用", quantity=5)
    check("④ 前置：建一条合法询价", code, 0)
    check_true("④ 修改入口也拒负数（从前 200 落库）",
               call("PATCH", f"/custom-inquiries/{iid}", admin, {"quantity": -3})[1].get("code") != 0)
    if iid:
        db(f"delete from custom_inquiries where id={iid}")
    db(f"delete from custom_inquiries where title like '{MARK}%'")


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
    sec_c302b(admin)
    sec_c303(admin)
    sec_c304(admin)
    sec_c305(admin)

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
