#!/usr/bin/env python
"""第十批组三：线索转化的并发幂等（10.1）、转化时的联系人复用（10.2）、
主联系人唯一（10.4）。

**只在隔离库跑**：库名必须含 test，且推送开关全关、**必须显式给 API_BASE**。

## 守的三件事

**10.1 转化必须真的幂等。** 从前 `convert_lead` 读线索用的是不加锁的
`get_visible_lead`：两个人同时点转化，**都会读到"还没转化"**，然后各建一套
客户/联系人 —— 幂等判断形同虚设。现在第一步就是 `lock_lead`（行锁 +
`populate_existing`），锁内重读才作数。

**10.2 转化不该建出重复联系人。** `LeadConvert` 从前既没有"复用哪个联系人"
的入口，提交时也不重查：同一个手机号会在同一个客户下出现两条。
现在新建之前先看这个客户下有没有同号/同邮箱的人，有就复用；
也支持显式指定 `reuse_contact_id`（只接受**已经挂在这个客户下**的）。

**10.4 一个客户只能有一个主联系人。** 库上加了部分唯一索引兜底
（`uq_contacts_primary_per_customer`，迁移 `c3f8a1d6e9b4`），应用层三个入口
（新建 / 更新 / 转化内部建）都在**写库之前**先锁住客户行 —— 不先锁就写，
并发下后一个会直接撞唯一索引报 500。

## 为什么新建套件

线索转化、联系人主次这两条链此前**没有任何接口级套件覆盖**
（`grep -rn "leads/.*convert" scripts/*.py` 在新建之前零命中）。

跑法（隔离库）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_iso_test_xxx \\
      PYTHONPATH=. .venv/bin/python scripts/check_tenth_round_conversion.py
"""

import asyncio
import json
import threading
import time
import urllib.error
import urllib.request
from _test_support import require_api_base, require_isolated_db

require_isolated_db()

import app.main  # noqa: F401  保证所有模型都注册进 metadata

_ = app.main  # 显式"用"一下：只 import 不带这一句，pyflakes 会当成未使用

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.database import SessionLocal

# 地址与库的防呆统一收在 _test_support（判据只留一处）
BASE = require_api_base()

PREFIX = f"CHKCONV{int(time.time())}"
FAILURES: list[str] = []


def check(label: str, actual, expected) -> None:
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: object = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
        FAILURES.append(label)


def call(method: str, path: str, token: str | None = None, body=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
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


def call_later(method: str, path: str, *, token: str, body=None) -> tuple[threading.Thread, dict]:
    """把一次真实请求放到后台线程发 —— 用来打真正的并发（手法同 check_recycle_bin）。"""
    slot: dict = {}

    def run() -> None:
        slot["result"] = call(method, path, token=token, body=body)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, slot


def _status(thread: threading.Thread, slot: dict) -> int:
    thread.join(timeout=30)
    result = slot.get("result")
    return result[0] if result else -1


class _RowLockHolder(threading.Thread):
    """另起一条连接、在一个显式事务里 `select ... for update` 锁住一行，等放行再回滚。

    用途：把"两个请求已经跑到某一步"这种瞬时状态**固定住**，让并发断言变成确定性的
    （不然只能靠"多发几次撞一撞"，修没修好都可能绿）。

    ⚠️ 必须**自建 NullPool 引擎**，不能复用应用那个全局 engine —— 它的连接池绑在
    主事件循环上，子线程里拿到会 `attached to a different loop`，而异常是在子线程里
    抛的、**完全静默**（量到 0 秒，得出"没加锁"的相反结论）。
    """

    def __init__(self, sql: str, params: dict):
        super().__init__(daemon=True)
        self._sql = sql
        self._params = params
        self.ready = threading.Event()
        self._release = threading.Event()
        self.error: str | None = None

    def run(self) -> None:
        async def go() -> None:
            eng = create_async_engine(settings.database_url, poolclass=NullPool)
            try:
                async with eng.connect() as conn:
                    await conn.execute(text(self._sql), self._params)
                    self.ready.set()
                    self._release.wait(30)
                    await conn.rollback()
            finally:
                await eng.dispose()

        try:
            asyncio.run(go())
        except Exception as exc:  # noqa: BLE001
            self.error = repr(exc)
            self.ready.set()

    def release(self) -> None:
        self._release.set()


async def _contact_state(customer_id: int) -> dict:
    """客户下联系人的（id 列表, 主联系人 id, 条数）。"""
    async with SessionLocal() as s:
        rows = (
            await s.execute(
                text(
                    "select id, is_primary from contacts"
                    " where customer_id = :c and deleted_at is null order by id"
                ),
                {"c": customer_id},
            )
        ).all()
    ids = [int(r[0]) for r in rows]
    primaries = [int(r[0]) for r in rows if r[1]]
    return {"ids": ids, "primary": primaries, "count": len(ids)}


async def _lead_converted_customer(lead_id: int) -> int | None:
    async with SessionLocal() as s:
        return (
            await s.execute(
                text("select converted_customer_id from leads where id = :l"), {"l": lead_id}
            )
        ).scalar_one_or_none()


async def cleanup() -> None:
    """清干净本套件建的客户 / 联系人 / 线索 / 合并留痕 / 审计。"""
    async with SessionLocal() as s:
        await s.execute(
            text(
                "delete from contacts where customer_id in "
                "(select id from customers where name like :p)"
            ),
            {"p": f"{PREFIX}%"},
        )
        # 合并留痕是**没有外键**的历史表（合并完来源客户会被物理删掉），
        # 必须自己按 id 收，否则会一直留在库里（守门套件会报）。
        targets = "(select id from customers where name like :p)"
        await s.execute(
            text(
                "delete from customer_merge_logs where source_customer_id in "
                + targets
                + " or target_customer_id in "
                + targets
            ),
            {"p": f"{PREFIX}%"},
        )
        await s.execute(
            text("delete from leads where name like :p or company_name like :p"),
            {"p": f"{PREFIX}%"},
        )
        await s.execute(
            text("delete from customer_owner_history where customer_id in "
                 "(select id from customers where name like :p)"),
            {"p": f"{PREFIX}%"},
        )
        await s.execute(text("delete from customers where name like :p"), {"p": f"{PREFIX}%"})
        await s.execute(
            text(
                "delete from audit_logs where business_type in"
                " ('lead', 'customer', 'contact')"
                " and (coalesce(before_data::text, '') like :m"
                "      or coalesce(after_data::text, '') like :m)"
            ),
            {"m": f"%{PREFIX}%"},
        )
        await s.commit()


async def main() -> int:
    admin = login("admin", "admin123")
    created_customers: list[int] = []
    created_leads: list[int] = []

    def api(method, path, body=None, expected=200, token=None):
        status, result = call(method, path, token=token or admin, body=body)
        assert status == expected, (method, path, status, result)
        return result.get("data")

    try:
        # ==================================================== 10.1 并发幂等
        print("=== 1. 10.1 转化并发：恰好一个成功，不能建出两个客户 ===")
        # 同一个线索连打三次并发，每轮都要落在"一个 200 + 一个 409"上。
        # 单轮是概率事件，多跑几轮才说明锁真的在起作用。
        for round_no in range(1, 4):
            lead = api("POST", "/leads", token=admin, body={
                "name": f"{PREFIX}并发线索{round_no}",
                "company_name": f"{PREFIX}并发公司{round_no}",
                "contact_name": "并发联系人",
                "mobile": f"1390000{round_no:04d}",
            })
            created_leads.append(lead["id"])

            t1, s1 = call_later("POST", f"/leads/{lead['id']}/convert", token=admin,
                                body={"customer_mode": "new"})
            t2, s2 = call_later("POST", f"/leads/{lead['id']}/convert", token=admin,
                                body={"customer_mode": "new"})
            codes = sorted([_status(t1, s1), _status(t2, s2)])
            check(f"第 {round_no} 轮：两个并发请求 = 一个成功 + 一个被拒", codes, [200, 409])

            converted = await _lead_converted_customer(lead["id"])
            if converted:
                created_customers.append(int(converted))
            # 这一条才是真正的判据：库里只该有一个客户挂在这条线索上
            async with SessionLocal() as s:
                same_name = (
                    await s.execute(
                        text("select count(*) from customers where name = :n and deleted_at is null"),
                        {"n": f"{PREFIX}并发公司{round_no}"},
                    )
                ).scalar_one()
            check(f"第 {round_no} 轮：该线索只建出一个客户（没建出两个）", int(same_name), 1)

        # 顺序幂等：再转一次必须还是 409
        status, res = call("POST", f"/leads/{created_leads[-1]}/convert", token=admin,
                           body={"customer_mode": "new"})
        check("已转化的线索再转一次 → 409（顺序幂等）", status, 409)

        # ==================================================== 10.2 联系人复用
        print("=== 2. 10.2 转化时复用已有联系人，不建重复的 ===")
        cust = api("POST", "/customers", token=admin, body={
            "name": f"{PREFIX}复用客户", "owner_id": None,
        })
        created_customers.append(cust["id"])
        primary_contact = api("POST", f"/customers/{cust['id']}/contacts", token=admin, body={
            "name": "张伟", "mobile": "13800000001", "is_primary": True,
        })
        before = await _contact_state(cust["id"])
        check("夹具：客户下已有一个联系人", before["count"], 1)

        # 同手机号的线索转进来 → 应该复用，不新增
        dup_lead = api("POST", "/leads", token=admin, body={
            "name": f"{PREFIX}同号线索", "company_name": f"{PREFIX}复用公司",
            "contact_name": "张伟（另一个写法）", "mobile": "13800000001",
        })
        created_leads.append(dup_lead["id"])
        status, res = call("POST", f"/leads/{dup_lead['id']}/convert", token=admin, body={
            "customer_mode": "existing", "customer_id": cust["id"], "create_contact": True,
        })
        check("同号线索转化成功", status, 200)
        after = await _contact_state(cust["id"])
        check("同一个客户下没有多出第二个同号联系人", after["count"], before["count"])
        check("转化的结果指向的是原来那条联系人",
              res.get("data", {}).get("contact_id"), primary_contact["id"])

        # 不同手机号 → 正常新建
        new_lead = api("POST", "/leads", token=admin, body={
            "name": f"{PREFIX}异号线索", "company_name": f"{PREFIX}复用公司",
            "contact_name": "李娜", "mobile": "13900000002",
        })
        created_leads.append(new_lead["id"])
        status, res = call("POST", f"/leads/{new_lead['id']}/convert", token=admin, body={
            "customer_mode": "existing", "customer_id": cust["id"], "create_contact": True,
        })
        check("异号线索转化成功", status, 200)
        grown = await _contact_state(cust["id"])
        check("不同手机号 → 正常新建一条", grown["count"], before["count"] + 1)

        # 显式指定复用
        reuse_lead = api("POST", "/leads", token=admin, body={
            "name": f"{PREFIX}指定复用线索", "company_name": f"{PREFIX}复用公司",
            "contact_name": "随便谁", "mobile": "13700000003",
        })
        created_leads.append(reuse_lead["id"])
        status, res = call("POST", f"/leads/{reuse_lead['id']}/convert", token=admin, body={
            "customer_mode": "existing", "customer_id": cust["id"],
            "reuse_contact_id": primary_contact["id"],
        })
        check("显式指定复用 → 成功", status, 200)
        check("用的是指定的那条", res.get("data", {}).get("contact_id"), primary_contact["id"])
        same = await _contact_state(cust["id"])
        check("没有因此多建联系人", same["count"], grown["count"])

        # 借转化复用**别人客户**的联系人 → 拒绝
        other = api("POST", "/customers", token=admin, body={
            "name": f"{PREFIX}别的客户", "owner_id": None,
        })
        created_customers.append(other["id"])
        # 给"别的客户"也放一条联系人（夹具：证明客户本身是好的）
        api("POST", f"/customers/{other['id']}/contacts", token=admin, body={
            "name": "外人", "mobile": "13600000004",
        })
        # ⚠️ 必须**另起一条线索**：上面那条已经转化过了，再打会先撞上"已转化"
        # 的 409，根本走不到要验的那道"联系人不在这个客户名下"。
        steal_lead = api("POST", "/leads", token=admin, body={
            "name": f"{PREFIX}越权复用线索", "company_name": f"{PREFIX}复用公司",
            "contact_name": "谁", "mobile": "13600000005",
        })
        created_leads.append(steal_lead["id"])
        status, res = call("POST", f"/leads/{steal_lead['id']}/convert", token=admin, body={
            "customer_mode": "existing", "customer_id": other["id"],
            "reuse_contact_id": primary_contact["id"],
        })
        check("复用「不在这个客户名下」的联系人 → 422（不许借转化改挂别人的联系人）",
              status, 422)

        # ==================================================== 10.2 复审反例：并发
        print()
        print("=== 2b. 两条「不同」线索并发转进同一个客户（复审 10.2）===")
        # 上面第 2 节测的是**串行**复用（先有联系人、再转一条同号线）—— 串行下查得到，
        # 看不出"查重留在锁外"这个漏。真会漏的是：两个请求**同时**查到"没有"，
        # 然后各建一条。这里用一把外部客户行锁把两边都钉在"已经进到这一步"，
        # 放行后只许留一条联系人。
        c6 = api("POST", "/customers", token=admin, body={
            "name": f"{PREFIX}并发转化客户", "owner_id": None,
        })
        created_customers.append(c6["id"])
        dup_mobile = "13600000001"
        l1 = api("POST", "/leads", token=admin, body={
            "name": f"{PREFIX}并发线1", "company_name": f"{PREFIX}并发公司",
            "contact_name": "同号甲", "mobile": dup_mobile,
        })
        l2 = api("POST", "/leads", token=admin, body={
            "name": f"{PREFIX}并发线2", "company_name": f"{PREFIX}并发公司",
            "contact_name": "同号乙", "mobile": dup_mobile,
        })
        created_leads.extend([l1["id"], l2["id"]])

        holder = _RowLockHolder(
            "select id from customers where id = :i for update", {"i": c6["id"]}
        )
        holder.start()
        holder.ready.wait(30)
        check_true("装置就绪：外部事务已锁住目标客户行", holder.ready, holder.error or "")

        rt1, rs1 = call_later("POST", f"/leads/{l1['id']}/convert", token=admin, body={
            "customer_mode": "existing", "customer_id": c6["id"], "create_contact": True,
        })
        rt2, rs2 = call_later("POST", f"/leads/{l2['id']}/convert", token=admin, body={
            "customer_mode": "existing", "customer_id": c6["id"], "create_contact": True,
        })
        time.sleep(1.5)
        check_true("两个转化请求都排在同一把客户行锁上（装置自检：确实在等锁）",
                   not rs1.get("result") and not rs2.get("result"), "见日志")
        holder.release()
        codes = sorted([_status(rt1, rs1), _status(rt2, rs2)])
        check_true("放行后两个转化都成功", codes == [200, 200], str(codes))
        merged = await _contact_state(c6["id"])
        check("同一客户下同号联系人**只留一条**（去重守住了）", merged["count"], 1)

        # ==================================================== 10.4 主联系人唯一
        print("=== 3. 10.4 一个客户只能有一个主联系人 ===")
        c2 = api("POST", "/customers", token=admin, body={
            "name": f"{PREFIX}主联系人客户", "owner_id": None,
        })
        created_customers.append(c2["id"])
        a = api("POST", f"/customers/{c2['id']}/contacts", token=admin, body={
            "name": "甲", "mobile": "13500000001", "is_primary": True,
        })
        state = await _contact_state(c2["id"])
        check("第一个设主的成为主联系人", state["primary"], [a["id"]])

        b = api("POST", f"/customers/{c2['id']}/contacts", token=admin, body={
            "name": "乙", "mobile": "13500000002", "is_primary": True,
        })
        state = await _contact_state(c2["id"])
        check("再来一个设主的：主位转移过去", state["primary"], [b["id"]])
        check("同客户的主联系人**恰好一个**", len(state["primary"]), 1)

        # 换回甲：走 set-primary 接口
        status, _ = call("POST", f"/contacts/{a['id']}/set-primary", token=admin)
        check("set-primary 把主位换回甲", status, 200)
        state = await _contact_state(c2["id"])
        check("换完之后仍然只有一个主", state["primary"], [a["id"]])

        # 把主联系人改挂到别的客户 → 主标记不能跟着走
        c3 = api("POST", "/customers", token=admin, body={
            "name": f"{PREFIX}接收客户", "owner_id": None,
        })
        created_customers.append(c3["id"])
        status, _ = call("POST", f"/contacts/{a['id']}/change-customer", token=admin, body={
            "customer_id": c3["id"],
        })
        check("把主联系人改挂到另一个客户", status, 200)
        moved = await _contact_state(c3["id"])
        check("改挂过去之后**不是**主联系人（主标记没跟着走）", moved["primary"], [])
        left = await _contact_state(c2["id"])
        check("原客户那边也没有留下「凭空多一个主」", left["primary"], [])

        # 库层兜底：绕过接口直接 SQL 插，也必须塞不进第二个主。
        # 这是那条部分唯一索引存在的意义 —— 挡住"绕过接口直接写库"和将来
        # 新增入口时漏掉的那一处。
        async with SessionLocal() as s:
            # ① 先给这个客户放一条主（此时它一条主都没有）
            await s.execute(
                text(
                    "insert into contacts (customer_id, name, mobile, is_primary)"
                    " values (:c, :n, :m, true)"
                ),
                {"c": c2["id"], "n": f"{PREFIX}硬塞的主", "m": "13500000009"},
            )
            await s.commit()
            # ② 再硬塞第二条主 → 必须被索引挡住
            violated = False
            try:
                await s.execute(
                    text(
                        "insert into contacts (customer_id, name, mobile, is_primary)"
                        " values (:c, :n, :m, true)"
                    ),
                    {"c": c2["id"], "n": f"{PREFIX}硬塞的主2", "m": "13500000010"},
                )
                await s.commit()
            except Exception as exc:  # noqa: BLE001
                violated = "uq_contacts_primary_per_customer" in str(exc)
                await s.rollback()
        check_true("库层唯一索引真的挡得住（绕过接口也塞不进第二个主）", violated, "见日志")

        # 并发设主两个：都要成功、但最终只能留一个主
        c4 = api("POST", "/customers", token=admin, body={
            "name": f"{PREFIX}并发设主客户", "owner_id": None,
        })
        created_customers.append(c4["id"])
        m1 = api("POST", f"/customers/{c4['id']}/contacts", token=admin,
                 body={"name": "并发甲", "mobile": "13500000011"})
        m2 = api("POST", f"/customers/{c4['id']}/contacts", token=admin,
                 body={"name": "并发乙", "mobile": "13500000012"})
        t1, s1 = call_later("POST", f"/contacts/{m1['id']}/set-primary", token=admin)
        t2, s2 = call_later("POST", f"/contacts/{m2['id']}/set-primary", token=admin)
        codes = sorted([_status(t1, s1), _status(t2, s2)])
        check_true("并发设主：两个请求都不该报 5xx", all(c == 200 for c in codes), str(codes))
        final = await _contact_state(c4["id"])
        check("并发设主之后，主联系人仍**恰好一个**", len(final["primary"]), 1)

        # ⚠️ 复审 10.4 的独立反例：目标客户**本来就有主**，而且改挂时**要求继续当主**。
        #    上面那条改挂用的是"没有主的客户 + 没要求当主"，恰好绕开了会翻车的组合 ——
        #    入口若先改归属、再腾主位，"腾位"那次查询会触发 autoflush，把
        #    "已挂到目标客户、却还带着主标记"的这条先写下去，撞唯一索引报 500。
        print()
        print("=== 3b. 改挂到「已有主的客户」并要求继续当主（复审 10.4 反例）===")

        async def _move_case(entry: str, tag: str) -> None:
            ca = api("POST", "/customers", token=admin,
                     body={"name": f"{PREFIX}反例{tag}甲", "owner_id": None})
            cb = api("POST", "/customers", token=admin,
                     body={"name": f"{PREFIX}反例{tag}乙", "owner_id": None})
            created_customers.extend([ca["id"], cb["id"]])
            ca_p = api("POST", f"/customers/{ca['id']}/contacts", token=admin,
                       body={"name": f"甲的主{tag}", "mobile": f"1351000{tag}",
                             "is_primary": True})
            cb_p = api("POST", f"/customers/{cb['id']}/contacts", token=admin,
                       body={"name": f"乙的主{tag}", "mobile": f"1352000{tag}",
                             "is_primary": True})
            status, res = call("POST", f"/contacts/{ca_p['id']}/{entry}", token=admin,
                               body={"customer_id": cb["id"], "is_primary": True})
            check(f"{entry}：两边都有主、要求继续当主 → 200（而不是 500）", status, 200)
            got = await _contact_state(cb["id"])
            check(f"{entry}：目标客户最终恰好一个主，且就是刚改挂过去的",
                  got["primary"], [ca_p["id"]])
            check_true(f"{entry}：目标客户原来的主被降级（没凑成两个主）",
                       cb_p["id"] not in got["primary"], str(got["primary"]))
            check(f"{entry}：原客户那边不再有主",
                  (await _contact_state(ca["id"]))["primary"], [])

        await _move_case("change-customer", "1")
        await _move_case("bind-customer", "2")

        # ---- 合并客户：两边的联系人都往目标并，主联系人不能跟着走 ----
        # 这一条是加"主联系人唯一"这条库约束时**顺手抓出来的真 bug**：
        # 合并原先"先改挂、后降级"，改挂那一刻目标名下就凑出两个主，
        # 索引直接把整次合并打成 500（`check_data_scope` 报出来的）。
        print()
        print("=== 4. 客户合并时，主联系人不能跟着并过来 ===")

        async def _merge_case(tag_name: str, target_gets_primary: bool) -> None:
            src = api("POST", "/customers", token=admin,
                      body={"name": f"{PREFIX}并入方{tag_name}", "owner_id": None})
            tgt = api("POST", "/customers", token=admin,
                      body={"name": f"{PREFIX}承接方{tag_name}", "owner_id": None})
            created_customers.extend([src["id"], tgt["id"]])
            s_contact = api("POST", f"/customers/{src['id']}/contacts", token=admin,
                            body={"name": f"并入的人{tag_name}", "mobile": f"1310000{tag_name}",
                                  "is_primary": True})
            if target_gets_primary:
                api("POST", f"/customers/{tgt['id']}/contacts", token=admin,
                    body={"name": f"目标的人{tag_name}", "mobile": f"1320000{tag_name}",
                          "is_primary": True})
            status, res = call("POST", "/customers/merge", token=admin, body={
                "source_customer_id": src["id"], "target_customer_id": tgt["id"],
            })
            check(f"（合并且目标{'有' if target_gets_primary else '没'}主）合并成功",
                  status, 200)
            state = await _contact_state(tgt["id"])
            check(f"（合并且目标{'有' if target_gets_primary else '没'}主）"
                  f"合并后主联系人恰好一个", len(state["primary"]), 1)
            if target_gets_primary:
                check_true("（目标原有主）保留的是目标那位，并过来的是普通联系人",
                           s_contact["id"] not in state["primary"], str(state["primary"]))
            else:
                check_true("（目标原本没主）并过来的那位顶上来了",
                           s_contact["id"] in state["primary"], str(state["primary"]))
            check("两边的人都在目标名下", state["count"], 2 if target_gets_primary else 1)

        await _merge_case("A", target_gets_primary=True)
        await _merge_case("B", target_gets_primary=False)

        # 顺带确认这些客户没进回收站（本套件的清理靠名字，不该有漏网）
        print()
        print("=== 5. 收尾自检 ===")
        async with SessionLocal() as s:
            leaked = (
                await s.execute(
                    text("select count(*) from customers where name like :p"), {"p": f"{PREFIX}%"}
                )
            ).scalar_one()
        check_true("夹具都还活着（说明下面 cleanup 真的删得掉）", int(leaked) > 0, int(leaked))

    finally:
        print()
        print("=== 清理 ===")
        await cleanup()
        print(f"  已清 CHKCONV 夹具（{PREFIX}）")

    if FAILURES:
        print(f"\n失败 {len(FAILURES)} 项：{FAILURES}")
        raise SystemExit(1)
    print("\nOK 转化并发幂等 / 联系人复用 / 主联系人唯一（第十批组三）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
