"""商机：阶段停用保护、需求明细校验、负责人三入口统一（第十二批第二批）。

跑法：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 PYTHONPATH=. \\
        .venv/bin/python scripts/check_opportunity_stage_item_guards.py

## 覆盖（12.4 / 12.5 / 12.8）

**12.4 阶段只能停用，不能删掉历史**
`DELETE /opportunity-stages/{id}` 从前是**真删行**，而"还有人用吗"只看了"此刻有没有
商机停在上面"—— 商机一推进走，阶段就被删，可它的名字还留在那些商机的阶段历史里，
于是历史里的"从哪来、到哪去"变成空白。现在改成停用：新业务不再选它，历史照旧显示。
（早年被物理删掉留下的空洞不猜名字，但要如实标出来。）

**12.5 需求明细的数值与 SKU**
新增 / 编辑 / 批量替换三处同一套规则：数量必须 >0（允许小数）、目标价不能为负、
SKU 必须先验存在（给清楚的错误，不再是撞外键的 500）；批量替换**先全验再执行**，
一条不合法不许把原明细删掉。

**12.8 商机负责人三入口统一**
新建 / 复制 / 改派都是"**任意在职员工**"（存在 + 在职），不吃数据范围；但来源业务
的权限照旧（新建要看得到客户、复制改派要能操作原商机）；继承来的负责人也要过同一关。

夹具前缀 `CHK12D`，开头先清残留、结尾再清一次。
"""

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request

from _test_support import require_api_base, require_isolated_db

require_isolated_db()

BASE = require_api_base()
DB_URL = os.environ["DATABASE_URL"]
FAILURES: list[str] = []
PREFIX = "CHK12D"


def check(label, actual, expected):
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label, condition, detail=""):
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
        FAILURES.append(label)


def call(method, path, token=None, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw) if raw else {}
        except Exception:
            return e.code, {"raw": raw[:200]}
    except Exception as e:
        return -1, {"error": str(e)}


def login(u, p):
    st, res = call("POST", "/auth/login", body={"username": u, "password": p})
    if st != 200:
        print("登录失败", u, st, res)
        sys.exit(1)
    return res["data"]["access_token"]


async def with_session(fn):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(DB_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(eng, expire_on_commit=False)() as s:
            return await fn(s)
    finally:
        await eng.dispose()


def run(fn):
    return asyncio.run(with_session(fn))


def db(sql, params=None):
    from sqlalchemy import text

    async def go(s):
        return (await s.execute(text(sql), params or {})).all()

    return run(go)


FIX: dict = {}


def cleanup():
    from sqlalchemy import text

    async def go(s):
        p = {"p": f"{PREFIX}%"}
        for sql in (
            "delete from opportunity_items where opportunity_id in "
            "(select id from opportunities where title like :p)",
            "delete from opportunity_stage_history where opportunity_id in "
            "(select id from opportunities where title like :p)",
            "delete from opportunities where title like :p",
            "delete from customers where name like :p",
            "delete from user_roles where user_id in "
            "(select id from users where username like :u)",
            "delete from role_permissions where role_id in "
            "(select id from roles where code like :c)",
            "delete from roles where code like :c",
            "delete from users where username like :u",
            "delete from opportunity_stages where code like :c",
        ):
            await s.execute(text(sql), p if ":p" in sql else {"u": f"{PREFIX}%", "c": f"{PREFIX}%"})
        await s.commit()

    run(go)


def build_fixtures():
    from sqlalchemy import select, text

    from app.core.security import hash_password
    from app.modules.customer.model import Customer
    from app.modules.opportunity.model import Opportunity
    from app.modules.user.model import Permission, Role, User, role_permissions, user_roles

    async def go(s):
        role = Role(code=f"{PREFIX}_SELF", name=f"{PREFIX}业务员", data_scope="self",
                    status="active")
        s.add(role)
        await s.flush()
        perm_ids = dict((await s.execute(select(Permission.code, Permission.id))).all())
        for code in ("opportunity:view", "opportunity:manage", "customer:view"):
            await s.execute(role_permissions.insert().values(
                role_id=role.id, permission_id=perm_ids[code]))
        me = User(username=f"{PREFIX}_self", name=f"{PREFIX}业务员",
                  password_hash=hash_password("123456"), status="active")
        retired = User(username=f"{PREFIX}_retired", name=f"{PREFIX}已停用",
                       password_hash=hash_password("123456"), status="inactive")
        s.add_all([me, retired])
        await s.flush()
        await s.execute(user_roles.insert().values(user_id=me.id, role_id=role.id))

        admin_id = (await s.execute(text("select id from users where username='admin'"))).scalar_one()
        cust = Customer(name=f"{PREFIX}客户", owner_id=me.id, created_by=me.id)
        s.add(cust)
        await s.flush()
        stage_id = (await s.execute(text(
            "select id from opportunity_stages where is_win is not true and is_loss is not true "
            "and status='active' order by sequence limit 1"))).scalar_one()
        won_id = (await s.execute(text(
            "select id from opportunity_stages where is_win is true limit 1"))).scalar_one()
        opp = Opportunity(customer_id=cust.id, title=f"{PREFIX}商机", stage_id=stage_id,
                          owner_id=me.id, status="open", created_by=me.id)
        s.add(opp)
        await s.flush()
        sku_id = (await s.execute(text("select id from skus limit 1"))).scalar_one_or_none()
        await s.commit()
        FIX.update(dict(me=me.id, retired=retired.id, admin=admin_id, cust=cust.id,
                        opp=opp.id, stage=stage_id, won=won_id, sku=sku_id))

    run(go)


# ---------------------------------------------------------------- 12.4
def sec_12_4(admin):
    print("\n=== 12.4 阶段只能停用：用过的阶段不许消失（历史仍要能看）===")
    st, res = call("POST", "/opportunity-stages", admin,
                   {"code": f"{PREFIX}_A", "name": f"{PREFIX}阶段A", "sequence": 50})
    stage = res.get("data") or {}
    st, res = call("POST", "/opportunities", admin,
                   {"customer_id": FIX["cust"], "title": f"{PREFIX}商机-阶段历史",
                    "owner_id": FIX["me"]})
    oid = (res.get("data") or {}).get("id")
    call("POST", f"/opportunities/{oid}/change-stage", admin, {"stage_id": stage["id"]})
    _, res = call("GET", "/opportunity-stages", admin)
    other = next((x for x in res["data"]
                  if not x["is_win"] and not x["is_loss"] and x["id"] != stage["id"]), None)
    call("POST", f"/opportunities/{oid}/change-stage", admin, {"stage_id": other["id"]})

    st_del, res_del = call("DELETE", f"/opportunity-stages/{stage['id']}", admin)
    check("删除曾用过的阶段 → 200（实际是停用）", st_del, 200)
    filled = db("select status from opportunity_stages where id = :i", {"i": stage["id"]})
    check("阶段行还在，状态变成停用", (filled[0][0] if filled else None), "inactive")

    _, res = call("GET", f"/opportunities/{oid}/stage-history", admin)
    rows = res.get("data") or []
    to_names = [r.get("to_stage") for r in rows]
    check_true("历史里的阶段名仍然显示（没被抹成空）",
               all(n is not None for n in to_names), to_names)
    check_true("历史里没有被标成「阶段已不存在」",
               not any(r.get("from_stage_missing") or r.get("to_stage_missing") for r in rows),
               [(r.get("from_stage_missing"), r.get("to_stage_missing")) for r in rows])

    st_again, _ = call("DELETE", f"/opportunity-stages/{stage['id']}", admin)
    check("重复停用是幂等的（200）", st_again, 200)

    st_won, _ = call("DELETE", f"/opportunity-stages/{FIX['won']}", admin)
    check("成交阶段不许停用", st_won, 422)

    # 停用之后不再被选为新商机的初始阶段（第 12.3 条的口径）
    st_new, res_new = call("POST", "/opportunities", admin,
                           {"customer_id": FIX["cust"], "title": f"{PREFIX}商机-停用检查",
                            "owner_id": FIX["me"]})
    check_true("停用阶段不会被选为初始阶段",
               (res_new.get("data") or {}).get("stage_id") != stage["id"],
               (res_new.get("data") or {}).get("stage_name"))
    del st_new


# ---------------------------------------------------------------- 12.5
def sec_12_5(admin):
    print("\n=== 12.5 需求明细：数量 / 目标价 / SKU 三处同一套规则 ===")
    oid, sku = FIX["opp"], FIX["sku"]
    for qty in (-5, 0):
        st, _ = call("POST", f"/opportunities/{oid}/items", admin,
                     {"sku_id": sku, "quantity": qty})
        check(f"新增数量 {qty} → 400", st, 400)
    st, _ = call("POST", f"/opportunities/{oid}/items", admin,
                 {"sku_id": sku, "quantity": 1, "target_price": -10})
    check("目标价 -10 → 400", st, 400)

    st, res = call("POST", f"/opportunities/{oid}/items", admin,
                   {"sku_id": sku, "quantity": 2.5})
    check("合法小数数量 2.5 → 200", st, 200)
    item_id = (res.get("data") or {}).get("id")

    st, _ = call("PATCH", f"/opportunity-items/{item_id}", admin, {"quantity": 0})
    check("编辑数量为 0 → 400", st, 400)
    st, _ = call("PATCH", f"/opportunity-items/{item_id}", admin, {"target_price": -1})
    check("编辑目标价为负 → 400", st, 400)

    st, _ = call("POST", f"/opportunities/{oid}/items/batch", admin,
                 {"items": [{"sku_id": sku, "quantity": -2}]})
    check("批量替换里的负数量 → 400", st, 400)

    st, _ = call("POST", f"/opportunities/{oid}/items", admin,
                 {"sku_id": 99999999, "quantity": 1})
    check("引用不存在的 SKU → 404（不再是 500）", st, 404)

    st, _ = call("POST", f"/opportunities/{oid}/items/batch", admin,
                 {"items": [{"sku_id": sku, "quantity": 1}, {"sku_id": 99999999, "quantity": 1}]})
    check("批量里混一条坏 SKU → 404", st, 404)
    left = db("select count(*) from opportunity_items where opportunity_id = :i", {"i": oid})
    check("批量失败后原明细完整保留（没被先删光）", int(left[0][0]), 1)

    # ---- 取值范围与小数位（2026-10-08 补：清单原文那半句「数值精度和范围要与
    #      数据库字段一致，非法数值应在写入前被拒绝」）----
    # 从前这两类会一路走到库：太大 → 撞 numeric 溢出报 **500**（用户只看到
    # "服务器内部错误"，完全不知道是自己填大了）；小数超三位 → 被库**静默四舍五入**
    # （填 1.23456、存成 1.235），用户看到的合计和自己填的对不上。
    st, res = call("POST", f"/opportunities/{oid}/items", admin,
                   {"sku_id": sku, "quantity": 1e17})
    check("数量超出列能装的范围 → 400（不是 500）", st, 400)
    check_true("提示说清是「太大」，不是笼统的「参数校验失败」",
               "太大" in (res.get("message") or ""), res.get("message"))
    check_true("提示里带的是中文字段名，不是英文 key",
               "数量" in (res.get("message") or ""), res.get("message"))

    st, res = call("POST", f"/opportunities/{oid}/items", admin,
                   {"sku_id": sku, "quantity": 1.23456})
    check("数量小数超过 3 位 → 400（不再悄悄四舍五入）", st, 400)
    check_true("提示说清是「小数位太多」",
               "小数" in (res.get("message") or ""), res.get("message"))

    st, res = call("POST", f"/opportunities/{oid}/items", admin,
                   {"sku_id": sku, "quantity": 1, "target_price": 1e20})
    check("目标价超出列能装的范围 → 400（不是 500）", st, 400)

    # 对照：合法小数照样存得住、且**原样**存下来（不是被改过的数）
    st, res = call("POST", f"/opportunities/{oid}/items", admin,
                   {"sku_id": sku, "quantity": 1.25})
    check("对照：合法小数 1.25 → 200", st, 200)
    new_id = (res.get("data") or {}).get("id")
    saved = db("select quantity from opportunity_items where id = :i", {"i": new_id})
    check("存进去就是填的那个数（没被改写）", str(saved[0][0]), "1.250")

    st, _ = call("PATCH", f"/opportunity-items/{new_id}", admin, {"quantity": 1e17})
    check("编辑入口也拦超范围（两个入口同一套）", st, 400)
    st, _ = call("POST", f"/opportunities/{oid}/items/batch", admin,
                 {"items": [{"sku_id": sku, "quantity": 1e17}]})
    check("批量替换也拦超范围（三个入口同一套）", st, 400)


# ---------------------------------------------------------------- 12.8
def sec_12_8(admin, t_me):
    print("\n=== 12.8 商机负责人：新建 / 复制 / 改派 同一把尺子 ===")
    st_create, _ = call("POST", "/opportunities", t_me,
                        {"customer_id": FIX["cust"], "title": f"{PREFIX}商机-新建给管理员",
                         "owner_id": FIX["admin"]})
    check("业务员新建商机指定管理员 → 200", st_create, 200)

    st_clone, _ = call("POST", f"/opportunities/{FIX['opp']}/clone", t_me,
                       {"title": f"{PREFIX}商机-复制给管理员", "owner_id": FIX["admin"]})
    check("复制商机指定管理员 → 200", st_clone, 200)

    st_assign, res_assign = call("POST", f"/opportunities/{FIX['opp']}/assign", t_me,
                                 {"owner_id": FIX["admin"]})
    check("改派商机给管理员 → 200", st_assign, 200)
    check("改派后负责人确实变了", (res_assign.get("data") or {}).get("owner_id"), FIX["admin"])
    check_true("改派留下了操作留痕（审计）",
               bool(db("select id from audit_logs where business_type='opportunity' "
                       "and business_id = :i and action='assign'", {"i": FIX["opp"]})),
               "audit_logs.assign")

    # 上一步已把这条商机改派给管理员，业务员此刻**看不见**它了（403 才对）——
    # 所以这两条用手册齐全的管理员来调，验的是"员工本身合不合格"。
    st_missing, _ = call("POST", f"/opportunities/{FIX['opp']}/assign", admin,
                         {"owner_id": 99999999})
    check("指定不存在的员工 → 404", st_missing, 404)
    st_retired, _ = call("POST", f"/opportunities/{FIX['opp']}/assign", admin,
                         {"owner_id": FIX["retired"]})
    check("指定已停用的员工 → 422", st_retired, 422)


def main():
    admin = login("admin", "admin123")
    cleanup()
    build_fixtures()
    t_me = login(f"{PREFIX}_self", "123456")
    print(f"夹具：客户#{FIX['cust']} 商机#{FIX['opp']} SKU#{FIX['sku']} 业务员#{FIX['me']}")
    try:
        sec_12_4(admin)
        sec_12_5(admin)
        sec_12_8(admin, t_me)
    finally:
        cleanup()
        print("\n夹具已清理")


if __name__ == "__main__":
    main()
    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项：")
        for f in FAILURES:
            print("  -", f)
        sys.exit(1)
    print("商机阶段 / 需求明细 / 负责人：全部通过")
