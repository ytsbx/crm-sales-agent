"""商机三道闸门：概览按模块权限过滤 / 推进阶段不得替代成交失单 / 阶段配置保存前校验。

第十二批第一批（12.1 / 12.2 / 12.3）。跑法：

    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 PYTHONPATH=. \\
        .venv/bin/python scripts/check_opportunity_gates.py

## 覆盖

**12.1 商机概览不能绕过各模块的权限与数据范围**
商机可见只证明能看到这条商机；概览里的报价/订单/任务/跟进要分别按各自模块的
查看权限与数据范围过滤，无权限的板块给 `counts.xxx = null` 并列入 `blocked`，
**不能**把"无权限"显示成"没有数据"。数量与列表必须同一套过滤（从前数量连
已删报价都算）。

**12.2 普通推进 / 旧成交口不得绕过成交与失单**
`change-stage` 遇到成交/失单阶段一律 422（成交走确认成交、失单走标记失单）；
旧 `/win` 与 `/confirm-win` 共用同一套校验（报价归属与当前版本、已发送、审批、
有效期、建单权限、转订单）；已失单的商机不能借任何成交入口越过状态。

**12.3 阶段配置不能把成交流程配坏**
成交标记最多一个、同一阶段不能又成交又失单（保存前拒绝）；取初始阶段排除
成交/失单与停用阶段。

夹具前缀 `CHK12G`，开头先清残留、结尾再清一次，可反复执行。
"""

import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal

from _test_support import require_api_base, require_isolated_db

require_isolated_db()

BASE = require_api_base()
DB_URL = os.environ["DATABASE_URL"]
FAILURES: list[str] = []
PREFIX = "CHK12G"
STAMP = str(int(time.time()))


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


FIX: dict = {}


def cleanup():
    from sqlalchemy import text

    async def go(s):
        p = {"p": f"{PREFIX}%"}
        # 成交会连带生成订单（单号是系统取号的，不带夹具前缀）→ 按客户关联找出来，
        # 再把**所有引用它的表**清掉（用 information_schema 动态查，免得漏了新加的表）
        order_ids = [
            row[0]
            for row in (
                await s.execute(text(
                    "select id from sales_orders where customer_id in "
                    "(select id from customers where name like :p)"), p)
            ).all()
        ]
        if order_ids:
            ids = ",".join(str(int(x)) for x in order_ids)
            refs = (
                await s.execute(text(
                    "select tc.table_name, kcu.column_name "
                    "from information_schema.table_constraints tc "
                    "join information_schema.key_column_usage kcu "
                    "  on kcu.constraint_name = tc.constraint_name "
                    "join information_schema.constraint_column_usage ccu "
                    "  on ccu.constraint_name = tc.constraint_name "
                    "where tc.constraint_type='FOREIGN KEY' "
                    "  and ccu.table_name='sales_orders'"
                ))
            ).all()
            for table, column in refs:
                await s.execute(text(f"delete from {table} where {column} in ({ids})"))
            await s.execute(text(f"delete from sales_orders where id in ({ids})"))

        await s.execute(text("update quotes set current_version_id = null where quote_no like :p"), p)
        await s.execute(text("delete from quote_versions where quote_id in "
                             "(select id from quotes where quote_no like :p)"), p)
        await s.execute(text("delete from quotes where quote_no like :p"), p)
        # 成交会在商机下写自动跟进留痕
        await s.execute(text("delete from followups where opportunity_id in "
                             "(select id from opportunities where title like :p)"), p)
        await s.execute(text("delete from opportunity_stage_history where opportunity_id in "
                             "(select id from opportunities where title like :p)"), p)
        await s.execute(text("delete from opportunities where title like :p"), p)
        await s.execute(text("delete from customers where name like :p"), p)
        await s.execute(text("delete from user_roles where user_id in "
                             "(select id from users where username like :u)"), {"u": f"{PREFIX}%"})
        await s.execute(text("delete from role_permissions where role_id in "
                             "(select id from roles where code like :c)"), {"c": f"{PREFIX}%"})
        await s.execute(text("delete from roles where code like :c"), {"c": f"{PREFIX}%"})
        await s.execute(text("delete from users where username like :u"), {"u": f"{PREFIX}%"})
        await s.execute(text("delete from opportunity_stages where code like :c"), {"c": f"{PREFIX}%"})
        await s.commit()

    run(go)


def build_fixtures():
    from sqlalchemy import select, text

    from app.core.security import hash_password
    from app.modules.customer.model import Customer
    from app.modules.opportunity.model import Opportunity
    from app.modules.quote.model import Quote, QuoteVersion
    from app.modules.user.model import (
        Permission,
        Role,
        User,
        role_permissions,
        user_roles,
    )

    async def go(s):
        r_opp = Role(code=f"{PREFIX}_OPPONLY", name=f"{PREFIX}仅商机", data_scope="all",
                     status="active")
        r_self = Role(code=f"{PREFIX}_SELF", name=f"{PREFIX}本人范围", data_scope="self",
                      status="active")
        s.add_all([r_opp, r_self])
        await s.flush()
        perm_ids = dict((await s.execute(select(Permission.code, Permission.id))).all())

        async def grant(role, codes):
            for code in codes:
                await s.execute(role_permissions.insert().values(
                    role_id=role.id, permission_id=perm_ids[code]))

        await grant(r_opp, ["opportunity:view", "opportunity:manage", "customer:view"])
        await grant(r_self, ["opportunity:view", "quote:view", "customer:view"])

        u1 = User(username=f"{PREFIX}_opponly", name=f"{PREFIX}仅商机",
                  password_hash=hash_password("123456"), status="active")
        u2 = User(username=f"{PREFIX}_self", name=f"{PREFIX}本人范围",
                  password_hash=hash_password("123456"), status="active")
        s.add_all([u1, u2])
        await s.flush()
        await s.execute(user_roles.insert().values(user_id=u1.id, role_id=r_opp.id))
        await s.execute(user_roles.insert().values(user_id=u2.id, role_id=r_self.id))

        zhang = (await s.execute(text("select id from users where username='zhangsan'"))).scalar_one()
        cust = Customer(name=f"{PREFIX}客户", owner_id=u2.id, created_by=u2.id)
        s.add(cust)
        await s.flush()

        stage_id = (await s.execute(text(
            "select id from opportunity_stages where is_win is not true and is_loss is not true "
            "and status='active' order by sequence limit 1"
        ))).scalar_one()
        won_stage_id = (await s.execute(text(
            "select id from opportunity_stages where is_win is true limit 1"))).scalar_one()
        loss_stage_id = (await s.execute(text(
            "select id from opportunity_stages where is_loss is true limit 1"))).scalar_one_or_none()
        loss_reason_id = (await s.execute(text("select id from loss_reasons limit 1"))).scalar_one()

        opp = Opportunity(customer_id=cust.id, title=f"{PREFIX}商机-别人的报价",
                          stage_id=stage_id, owner_id=u2.id, status="open", created_by=u2.id)
        opp_empty = Opportunity(customer_id=cust.id, title=f"{PREFIX}商机-空",
                                stage_id=stage_id, owner_id=u2.id, status="open", created_by=u2.id)
        opp_ok = Opportunity(customer_id=cust.id, title=f"{PREFIX}商机-可成交",
                             stage_id=stage_id, owner_id=u2.id, status="open", created_by=u2.id)
        s.add_all([opp, opp_empty, opp_ok])
        await s.flush()

        async def mk_quote(no, owner_id, amount, deleted=False):
            q = Quote(quote_no=no, customer_id=cust.id, opportunity_id=opp.id, owner_id=owner_id,
                      status="sent", created_by=owner_id, created_at=datetime.now(UTC))
            s.add(q)
            await s.flush()
            v = QuoteVersion(quote_id=q.id, version_no=1, total_amount=Decimal(str(amount)),
                             currency="CNY", approval_status="approved",
                             sent_at=datetime.now(UTC), created_by=owner_id,
                             created_at=datetime.now(UTC))
            s.add(v)
            await s.flush()
            q.current_version_id = v.id
            if deleted:
                q.deleted_at = datetime.now(UTC)
            return q, v

        q_other, _ = await mk_quote(f"{PREFIX}-Q-OTHER", zhang, 73123)
        q_mine, _ = await mk_quote(f"{PREFIX}-Q-MINE", u2.id, 18654, deleted=True)

        # 可成交商机：一条已发送、审批通过、当前版本的报价
        q_ok = Quote(quote_no=f"{PREFIX}-Q-OK", customer_id=cust.id, opportunity_id=opp_ok.id,
                     owner_id=u2.id, status="sent", created_by=u2.id, created_at=datetime.now(UTC))
        s.add(q_ok)
        await s.flush()
        v_ok = QuoteVersion(quote_id=q_ok.id, version_no=1, total_amount=Decimal("5000"),
                            currency="CNY", approval_status="approved",
                            sent_at=datetime.now(UTC), created_by=u2.id,
                            created_at=datetime.now(UTC))
        s.add(v_ok)
        await s.flush()
        q_ok.current_version_id = v_ok.id

        await s.commit()
        FIX.update(dict(u1=u1.id, u2=u2.id, cust=cust.id, opp=opp.id, opp_empty=opp_empty.id,
                        opp_ok=opp_ok.id, stage=stage_id, won_stage=won_stage_id,
                        loss_stage=loss_stage_id, loss_reason=loss_reason_id,
                        q_other=q_other.id, q_ok=v_ok.id))

    run(go)


def sec_12_1(t_opponly, t_self, admin):
    print("\n=== 12.1 商机概览：按模块权限与数据范围过滤 ===")
    opp = FIX["opp"]
    st, res = call("GET", f"/opportunities/{opp}/overview", t_opponly)
    data = res.get("data") or {}
    counts = data.get("counts") or {}
    check("没有报价权限 → counts.quotes 为 null（不是 0）", counts.get("quotes"), None)
    check_true("无权限板块被点名（blocked 含 quotes）",
               "quotes" in (data.get("blocked") or []), data.get("blocked"))
    check("无权限时报价列表为空", len(data.get("quotes") or []), 0)

    st2, res2 = call("GET", f"/opportunities/{opp}/overview", t_self)
    d2 = res2.get("data") or {}
    amounts = [q.get("current_version_amount") for q in (d2.get("quotes") or [])]
    check_true("本人范围账号在概览里看不到其他负责人的报价", 73123.0 not in amounts, amounts)
    check("数量与列表用同一套过滤（条数一致）",
          (d2.get("counts") or {}).get("quotes"), len(d2.get("quotes") or []))
    check("已删报价不算进概览数量", (d2.get("counts") or {}).get("quotes"), 0)

    # 对照：管理员仍能正常看到
    st3, res3 = call("GET", f"/opportunities/{opp}/overview", admin)
    d3 = res3.get("data") or {}
    check("对照：管理员能看到全部授权数据（含别人那条报价）",
          (d3.get("counts") or {}).get("quotes"), 1)


def sec_12_2(admin):
    print("\n=== 12.2 推进阶段 / 旧成交口不得绕过成交与失单 ===")
    st, _ = call("POST", f"/opportunities/{FIX['opp_empty']}/change-stage", admin,
                 {"stage_id": FIX["won_stage"]})
    check("没有报价的商机不能靠「推进阶段」直达成交", st, 422)
    if FIX["loss_stage"]:
        st, _ = call("POST", f"/opportunities/{FIX['opp_empty']}/change-stage", admin,
                     {"stage_id": FIX["loss_stage"]})
        check("也不能靠「推进阶段」直达失单", st, 422)

    st, _ = call("POST", f"/opportunities/{FIX['opp_empty']}/win", admin, {})
    check_true("旧成交口不传报价版本被拒（4xx）", 400 <= st < 500, f"HTTP {st}")

    call("POST", f"/opportunities/{FIX['opp_empty']}/lose", admin,
         {"loss_reason_id": FIX["loss_reason"], "remark": f"{PREFIX}测试失单"})
    st, _ = call("POST", f"/opportunities/{FIX['opp_empty']}/win", admin, {})
    check("失单之后不能借成交入口越过状态", st, 422)

    # 对照：有合法报价时，旧口子照常能成交并建单（两个入口同一套）
    st, res = call("POST", f"/opportunities/{FIX['opp_ok']}/win", admin,
                   {"win_quote_version_id": FIX["q_ok"]})
    check("对照：有有效报价时旧入口正常成交", st, 200)
    check_true("成交连带生成了订单（与确认成交同一套流程）",
               bool((res.get("data") or {}).get("order_id")),
               (res.get("data") or {}).get("order_no"))


def sec_12_3(admin):
    print("\n=== 12.3 阶段配置保存前校验 ===")
    st, res = call("POST", "/opportunity-stages", admin,
                   {"code": f"{PREFIX}_WIN2", "name": f"{PREFIX}第二个成交阶段",
                    "is_win": True, "sequence": 99})
    check("新增第二个「成交」阶段被拒", st, 422)
    check_true("拒绝理由说清是「只能有一个成交阶段」",
               "成交阶段" in str(res.get("message") or ""), res.get("message"))

    # 直接给现有成交阶段加上失单标记 → 同一行又成交又失单，必须拦
    st, res = call("GET", "/opportunity-stages", admin)
    win = next((x for x in (res.get("data") or []) if x.get("is_win")), None)
    st, res = call("PATCH", f"/opportunity-stages/{win['id']}", admin, {"is_loss": True})
    check("同一阶段不许既是成交又是失单", st, 422)
    check_true("拒绝理由说清「不能同时是成交和失单」",
               "同时" in str(res.get("message") or ""), res.get("message"))

    # 把成交阶段排到最前，新建商机也不该停在成交上
    if win:
        call("PATCH", f"/opportunity-stages/{win['id']}", admin, {"sequence": -1})
        st_c, res_c = call("POST", "/opportunities", admin,
                           {"customer_id": FIX["cust"], "title": f"{PREFIX}商机-初始阶段"})
        d = res_c.get("data") or {}
        check_true("成交阶段排最前，新商机也不会以它为初始阶段",
                   st_c == 200 and d.get("stage_id") != win["id"],
                   f"初始阶段={d.get('stage_name')}")
        call("PATCH", f"/opportunity-stages/{win['id']}", admin, {"sequence": 99})

    # 停用阶段不进新业务
    st, res = call("POST", "/opportunity-stages", admin,
                   {"code": f"{PREFIX}_OFF", "name": f"{PREFIX}停用阶段", "sequence": -5})
    if st == 200:
        off_id = (res.get("data") or {}).get("id")
        call("PATCH", f"/opportunity-stages/{off_id}", admin, {"status": "inactive"})
        st_c, res_c = call("POST", "/opportunities", admin,
                           {"customer_id": FIX["cust"], "title": f"{PREFIX}商机-停用检查"})
        d = res_c.get("data") or {}
        check_true("停用阶段不会被选为初始阶段",
                   d.get("stage_id") != off_id, f"初始阶段={d.get('stage_name')}")
    else:
        check("新增普通阶段可用（前置）", st, 200)


def main():
    admin = login("admin", "admin123")
    cleanup()
    build_fixtures()
    t1 = login(f"{PREFIX}_opponly", "123456")
    t2 = login(f"{PREFIX}_self", "123456")
    try:
        sec_12_1(t1, t2, admin)
        sec_12_2(admin)
        sec_12_3(admin)
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
    print("商机闸门：全部通过")
