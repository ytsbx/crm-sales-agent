"""公海/线索的取数与授权 + 领取并发（返工单 6.1 / 6.2，2026-10-06）。

**只在隔离库跑**：库名必须含 test（或 CI=true），且推送开关全关。

## 这两条修的是什么

**6.1 越权旁路**：同一个动作有两套取数 ——
单条入口用带数据范围校验的 `get_visible_customer` / `get_visible_lead`，
公海与批量入口却用 `get_customer_or_404` / `get_lead_or_404` / 裸 `session.get`（只判存在）。
于是"本人仅自己"范围的业务员拿 id 就能把别人名下的**私有**客户改给自己。
本套件逐条钉住：公海对象按公海规则（人人可领），私有对象按操作者数据范围。

**6.2 领取并发与条件统一**：领取是"读当前归属 → 判断无主 → 改成自己"三步，
没有行锁 —— 两个人同时读到公海状态时都会通过检查、先后覆盖负责人，还各写一条领取历史。
两条领取路径的检查项也已经漂移（公海那边查两个条件、客户那边只查一个）。
线索侧另外漏了状态：已转客户 / 已废弃的线索只要没有负责人就能被领走。

`FOR UPDATE` 的效果没法用"断言两个请求都返回 200"来反证，所以这里**真的并发打**：
两个线程用 Barrier 同时发，断言**恰好一个成功**。

跑法（需要后端在跑）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_sales_agent_test \\
      PYTHONPATH=. .venv/bin/python scripts/check_pool_scope_concurrency.py
"""

import asyncio
import json
import os
import threading
import time
import urllib.error
import urllib.request

from sqlalchemy import select, text

from app.core.database import SessionLocal

FAILURES: list[str] = []
BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000/api/v1")
PREFIX = "CHKPOOL"
#: 被拒的两种正常表现：403（数据范围/权限）或 404（不可见时不暴露存在性）
DENIED = (403, 404)


def check(label: str, actual, expected) -> None:
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: str = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}' + (f"：{detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def check_denied(label: str, status: int) -> None:
    good = status in DENIED
    print(f'  {"OK  " if good else "FAIL"} {label}: HTTP {status}（期望 403/404）')
    if not good:
        FAILURES.append(label)


def call(method: str, path: str, token: str | None = None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode() or "{}")
        except Exception:
            return exc.code, {}


def login(username: str, password: str) -> str:
    _, res = call("POST", "/auth/login", body={"username": username, "password": password})
    if res.get("code") != 0:
        raise SystemExit(f"登录失败（{username}）：{res.get('message')}")
    return res["data"]["access_token"]


async def cleanup() -> None:
    """按名字前缀清干净，**自底向上**。

    顺序上最容易漏的是"历史 / 变更"类副表：`customer_owner_history`
    引用 customers、`lead_assignments` 引用 leads，
    不先清就会让后面的删客户/删线索撞外键。
    """
    async with SessionLocal() as s:
        for sql in (
            "delete from customer_owner_history where customer_id in "
            "(select id from customers where name like :p)",
            "delete from lead_assignments where lead_id in "
            "(select id from leads where name like :p)",
            "delete from audit_logs where business_type = 'customer' and business_id in "
            "(select id from customers where name like :p)",
            "delete from audit_logs where business_type = 'lead' and business_id in "
            "(select id from leads where name like :p)",
            "delete from customers where name like :p",
            "delete from leads where name like :p",
            "delete from user_roles where user_id in (select id from users where username like :u)",
            "delete from user_roles where role_id in (select id from roles where code like :r)",
            "delete from role_permissions where role_id in (select id from roles where code like :r)",
            "delete from roles where code like :r",
            "delete from users where username like :u",
            "delete from departments where name like :p",
        ):
            await s.execute(
                text(sql), {"p": f"{PREFIX}%", "u": f"{PREFIX.lower()}%", "r": f"{PREFIX}%"}
            )
        await s.commit()


async def main() -> int:
    from app.core.security import hash_password
    from app.modules.customer.model import Customer
    from app.modules.lead.model import Lead
    from app.modules.user.model import (
        Department,
        Role,
        User,
        role_permissions,
        user_roles,
    )

    stamp = int(time.time())
    await cleanup()

    #: 部门经理是这次并发测试的主角：两个**同部门**的部门范围账号，
    #: 才能在"其中一个领走之后"仍然看得见那条客户 —— 否则输家会被
    #: 数据范围拒掉（403），就测不出"行锁拦住了第二个领取者"这件事。
    pwd = hash_password("123456")

    async with SessionLocal() as s:
        dept_a = Department(name=f"{PREFIX}甲部-{stamp}", status="active")
        dept_b = Department(name=f"{PREFIX}乙部-{stamp}", status="active")
        s.add_all([dept_a, dept_b])
        await s.flush()

        def make_user(name: str, dept_id: int | None = None) -> User:
            return User(
                name=f"{PREFIX}{name}-{stamp}",
                username=f"{PREFIX.lower()}_{name}_{stamp}",
                password_hash=pwd, status="active", department_id=dept_id,
            )

        boss = make_user("owner", dept_a.id)          # 私有客户的现任负责人
        rusher = make_user("rusher", dept_a.id)       # 本人范围，试图越权/领取
        outsider = make_user("outsider", dept_b.id)   # 另一个部门的本人范围
        mgr1 = make_user("mgr1", dept_a.id)           # 部门范围经理（并发主角 1）
        mgr2 = make_user("mgr2", dept_a.id)           # 部门范围经理（并发主角 2）
        s.add_all([boss, rusher, outsider, mgr1, mgr2])
        await s.flush()

        # 两个探针角色：
        # ① self 范围 + 查看/分配权限 → 用来证明"403 来自数据范围，不是缺权限"
        # ② department 范围 + 同样权限 → 用来测合法部门分配与并发
        self_role = Role(
            code=f"{PREFIX}SELF{stamp}", name=f"{PREFIX}本人范围探针", data_scope="self"
        )
        dept_role = Role(
            code=f"{PREFIX}DEPT{stamp}", name=f"{PREFIX}部门范围探针", data_scope="department"
        )
        s.add_all([self_role, dept_role])
        await s.flush()
        for role in (self_role, dept_role):
            for code in ("customer:view", "customer:assign", "lead:view", "lead:assign"):
                permission_id = (
                    await s.execute(
                        text("select id from permissions where code = :code"), {"code": code}
                    )
                ).scalar_one()
                await s.execute(
                    role_permissions.insert().values(role_id=role.id, permission_id=permission_id)
                )
        for user, role in (
            (rusher, self_role), (outsider, self_role),
            (boss, self_role), (mgr1, dept_role), (mgr2, dept_role),
        ):
            await s.execute(user_roles.insert().values(user_id=user.id, role_id=role.id))

        # ---- 夹具 ----
        priv_cust = Customer(                      # 别人的私有客户：不许被公海路径改走
            name=f"{PREFIX}私有客户-{stamp}", level="A", status="active",
            pool_status="private", owner_id=boss.id,
        )
        pub_cust = Customer(                       # 公海客户（走客户详情那条领取路径）
            name=f"{PREFIX}公海客户-{stamp}", level="B", status="active",
            pool_status="public", owner_id=None,
        )
        pub_cust2 = Customer(                      # 公海客户（走公海页面那条领取路径）
            name=f"{PREFIX}公海客户乙-{stamp}", level="B", status="active",
            pool_status="public", owner_id=None,
        )
        race_cust = Customer(                      # 并发抢的那条
            name=f"{PREFIX}并发客户-{stamp}", level="C", status="active",
            pool_status="public", owner_id=None,
        )
        dept_cust = Customer(                      # 甲部成员名下，供部门经理合法分配
            name=f"{PREFIX}部门客户-{stamp}", level="C", status="active",
            pool_status="private", owner_id=rusher.id,
        )
        cross_cust = Customer(                     # 乙部成员名下，跨部门不许动
            name=f"{PREFIX}跨部门客户-{stamp}", level="C", status="active",
            pool_status="private", owner_id=outsider.id,
        )
        priv_lead = Lead(                          # 别人的线索
            name=f"{PREFIX}私有线索-{stamp}", status="assigned", owner_id=boss.id,
        )
        batch_ok_lead = Lead(                      # 批量里合法的那条
            name=f"{PREFIX}批量合法线索-{stamp}", status="pending", owner_id=None,
        )
        conv_lead = Lead(                          # 已转客户：没有负责人也不许领
            name=f"{PREFIX}已转客户线索-{stamp}", status="converted", owner_id=None,
        )
        stale_lead = Lead(                         # 已废弃但没软删（脏数据形态）
            name=f"{PREFIX}已废弃线索-{stamp}", status="invalid", owner_id=None,
        )
        race_lead = Lead(                          # 并发抢的线索（先用后放回池）
            name=f"{PREFIX}并发线索-{stamp}", status="pending", owner_id=None,
        )
        race_lead2 = Lead(                         # 并发抢的线索（专用，不被前面动过）
            name=f"{PREFIX}并发线索乙-{stamp}", status="pending", owner_id=None,
        )
        s.add_all([
            priv_cust, pub_cust, pub_cust2, race_cust, dept_cust, cross_cust,
            priv_lead, batch_ok_lead, conv_lead, stale_lead, race_lead, race_lead2,
        ])
        await s.flush()
        # 已转客户的线索指向一张真实客户（真实数据的形态），否则用例跑的是
        # "一个不可能存在的组合"，拦下来也说明不了什么
        conv_lead.converted_customer_id = priv_cust.id
        ids = {
            "priv_cust": priv_cust.id, "pub_cust": pub_cust.id, "pub_cust2": pub_cust2.id,
            "race_cust": race_cust.id, "dept_cust": dept_cust.id, "cross_cust": cross_cust.id,
            "priv_lead": priv_lead.id, "batch_ok_lead": batch_ok_lead.id,
            "conv_lead": conv_lead.id, "stale_lead": stale_lead.id,
            "race_lead": race_lead.id, "race_lead2": race_lead2.id,
        }
        user_ids = {
            "boss": boss.id, "rusher": rusher.id, "outsider": outsider.id,
            "mgr1": mgr1.id, "mgr2": mgr2.id,
        }
        await s.commit()

    rusher_token = login(f"{PREFIX.lower()}_rusher_{stamp}", "123456")
    mgr1_token = login(f"{PREFIX.lower()}_mgr1_{stamp}", "123456")
    mgr2_token = login(f"{PREFIX.lower()}_mgr2_{stamp}", "123456")

    async def owner_of(customer_id: int):
        async with SessionLocal() as s:
            return (
                await s.execute(select(Customer.owner_id).where(Customer.id == customer_id))
            ).scalar_one()

    async def history_count(customer_id: int) -> int:
        async with SessionLocal() as s:
            return int(
                (
                    await s.execute(
                        text(
                            "select count(*) from customer_owner_history where customer_id = :c"
                        ),
                        {"c": customer_id},
                    )
                ).scalar_one()
            )

    async def lead_owner(lead_id: int):
        async with SessionLocal() as s:
            return (
                await s.execute(select(Lead.owner_id).where(Lead.id == lead_id))
            ).scalar_one()

    async def assign_count(lead_id: int) -> int:
        async with SessionLocal() as s:
            return int(
                (
                    await s.execute(
                        text("select count(*) from lead_assignments where lead_id = :l"),
                        {"l": lead_id},
                    )
                ).scalar_one()
            )

    try:
        print("=== 1. 6.1 公海路径不能改走别人的私有客户 ===")
        status, res = call(
            "POST", f'/public-pool/customers/{ids["priv_cust"]}/assign',
            rusher_token, {"owner_id": user_ids["rusher"], "reason": "越权尝试"},
        )
        check_denied("公海指派：本人范围改别人私有客户", status)
        check("被拒后客户负责人没变", await owner_of(ids["priv_cust"]), user_ids["boss"])
        check("被拒的越权没有留下归属变更历史", await history_count(ids["priv_cust"]), 0)

        status, _ = call(
            "POST", f'/public-pool/customers/{ids["priv_cust"]}/assign',
            rusher_token, {"owner_id": None, "reason": "越权放公海"},
        )
        check_denied("公海指派：把别人的私有客户放回公海", status)
        check("仍然没有归属变更历史", await history_count(ids["priv_cust"]), 0)

        status, _ = call(
            "POST", f'/public-pool/customers/{ids["priv_cust"]}/claim', rusher_token
        )
        check_denied("公海领取：别人的私有客户不能领", status)
        status, _ = call("POST", f'/customers/{ids["priv_cust"]}/claim', rusher_token)
        check_denied("客户详情领取：同一件事同样被拒（两条路径一致）", status)

        print("=== 2. 6.1 线索侧：单条 / 公海 / 批量三种入口同一口径 ===")
        status, _ = call(
            "POST", f'/public-pool/leads/{ids["priv_lead"]}/assign',
            rusher_token, {"owner_id": user_ids["rusher"], "reason": "越权尝试"},
        )
        check_denied("公海指派线索：本人范围改别人的线索", status)
        check("被拒后线索负责人没变", await lead_owner(ids["priv_lead"]), user_ids["boss"])

        status, _ = call("POST", f'/public-pool/leads/{ids["priv_lead"]}/claim', rusher_token)
        check_denied("公海领取线索：别人的线索不能领", status)

        # 单条入口（对照）：同一个线索，同一个人，应当给同样的拒绝
        status, single = call(
            "POST", f'/leads/{ids["priv_lead"]}/assign',
            rusher_token, {"owner_id": user_ids["rusher"]},
        )
        check_denied("单条入口：同一条线索同样被拒", status)

        status, batch = call(
            "POST", "/leads/batch-assign", rusher_token,
            {"lead_ids": [ids["batch_ok_lead"], ids["priv_lead"]],
             "owner_id": user_ids["rusher"], "reason": "批量越权尝试"},
        )
        check("批量分配整体成功返回（逐条报结果）", status, 200)
        data = (batch.get("data") or {})
        check_true("批量里合法的那条分出去了",
                   ids["batch_ok_lead"] in (data.get("assigned") or []),
                   str(data.get("assigned")))
        skipped = {row["lead_id"]: row for row in (data.get("skipped") or [])}
        check_true("越权的那条被跳过", ids["priv_lead"] in skipped, str(skipped))
        check("跳过的原因码与单条入口一致（40302 数据范围）",
              (skipped.get(ids["priv_lead"]) or {}).get("code"), 40302)
        check("被拒的线索负责人没变", await lead_owner(ids["priv_lead"]), user_ids["boss"])
        check("被拒的线索没有留下分配历史", await assign_count(ids["priv_lead"]), 0)
        check("合法的那条确实分给了本人", await lead_owner(ids["batch_ok_lead"]),
              user_ids["rusher"])

        print("=== 3. 6.1 部门范围：跨部门被拒、本部门合法分配仍可用 ===")
        status, _ = call(
            "POST", f'/public-pool/customers/{ids["cross_cust"]}/assign',
            mgr1_token, {"owner_id": user_ids["mgr1"], "reason": "跨部门越权"},
        )
        check_denied("部门经理：不能动乙部成员的客户", status)
        check("跨部门客户的负责人没变", await owner_of(ids["cross_cust"]),
              user_ids["outsider"])

        status, _ = call(
            "POST", f'/public-pool/customers/{ids["dept_cust"]}/assign',
            mgr1_token, {"owner_id": user_ids["boss"], "reason": "部门内部调度"},
        )
        check("部门经理：本部门内的分配仍然可用", status, 200)
        check("分配确实生效了", await owner_of(ids["dept_cust"]), user_ids["boss"])

        print("=== 4. 6.2 领取：幂等 + 已被领走 + 两条路径一致 ===")
        status, res = call("POST", f'/customers/{ids["pub_cust"]}/claim', rusher_token)
        check("合法领取公海客户成功", status, 200)
        check("负责人变成领取人", await owner_of(ids["pub_cust"]), user_ids["rusher"])
        check("只写了一条归属变更历史", await history_count(ids["pub_cust"]), 1)

        status, res = call("POST", f'/customers/{ids["pub_cust"]}/claim', rusher_token)
        check("同一请求重试仍返回成功（幂等）", status, 200)
        check("重试**不再增加**归属变更历史", await history_count(ids["pub_cust"]), 1)

        # 换一条路径领另一条公海客户，验证两条路径行为一致
        status, res = call(
            "POST", f'/public-pool/customers/{ids["pub_cust2"]}/claim', rusher_token
        )
        check("公海页面的领取路径同样可用", status, 200)
        check("两条路径结果一致（负责人相同、都只记一条历史）",
              (await owner_of(ids["pub_cust2"]), await history_count(ids["pub_cust2"])),
              (user_ids["rusher"], 1))

        # 已经被领走、但我看得见 → 明确报"被谁领了"（不是静默、也不是抢走）
        status, res = call("POST", f'/public-pool/customers/{ids["pub_cust"]}/claim', mgr1_token)
        check("已被他人领走的客户：不能重复领", status, 409)
        check_true("且提示里说清了被谁领走", "领取" in str(res.get("message")), str(res.get("message")))

        print("=== 5. 6.2 线索领取：状态条件与幂等 ===")
        # 这两条**不能只看"有没有负责人"**：已转客户与已废弃的线索都可能没有负责人，
        # 一领就把"它已经变成客户了"这个事实盖掉。
        # 拒绝码是 **HTTP 400 + 40002**：本项目对"业务规则不允许"（STATUS_NOT_ALLOWED）
        # 一律用 400，422 留给需要用户改参数的情形（照抄旧行为，不改动既有语义）。
        status, res = call("POST", f'/leads/{ids["conv_lead"]}/claim', rusher_token)
        check("已转客户的线索不能领取", status, 400)
        check("拒绝码是「状态不允许」", (res or {}).get("code"), 40002)
        check_true("且提示说清了它现在是什么状态",
                   "已转客户" in str((res or {}).get("message")), str((res or {}).get("message")))
        status, res = call("POST", f'/leads/{ids["stale_lead"]}/claim', rusher_token)
        check("已废弃的线索不能领取", status, 400)
        check("拒绝码同样是「状态不允许」", (res or {}).get("code"), 40002)

        # 对照：同样没人负责、但状态正常的线索，领取必须成功 ——
        # 否则上面两条"被拒"就可能只是权限问题，证明不了状态条件在起作用。
        status, res = call("POST", f'/leads/{ids["race_lead"]}/claim', rusher_token)
        check("（对照）没有负责人且状态正常的线索领取成功", status, 200)
        check("负责人变成领取人", await lead_owner(ids["race_lead"]), user_ids["rusher"])
        before_retry = await assign_count(ids["race_lead"])
        status, _ = call("POST", f'/leads/{ids["race_lead"]}/claim', rusher_token)
        check("线索领取重试仍成功（幂等）", status, 200)
        check("重试不增加分配历史", await assign_count(ids["race_lead"]), before_retry)

        print("=== 6. 6.2 真并发：两个人同时抢，恰好一个成功 ===")
        # 两个**同部门**的部门范围经理：谁抢到之后，另一个人仍然看得见这条客户，
        # 所以"没抢到"只会来自归属已被占用，而不是被数据范围挡掉 ——
        # 这样才真的测到行锁，而不是被 403 掩盖过去。
        barrier = threading.Barrier(2)

        def race(path: str, token: str):
            barrier.wait()  # 两个线程对齐后一起发，尽量重叠
            return call("POST", path, token)

        results = await asyncio.gather(
            asyncio.to_thread(race, f'/public-pool/customers/{ids["race_cust"]}/claim', mgr1_token),
            asyncio.to_thread(race, f'/public-pool/customers/{ids["race_cust"]}/claim', mgr2_token),
        )
        codes = sorted(r[0] for r in results)
        check_true("并发领取客户：恰好一个成功",
                   codes.count(200) == 1, f"两个请求分别返回 {codes}")
        winner_owner = await owner_of(ids["race_cust"])
        check_true("抢到的那个人就是负责人",
                   winner_owner in (user_ids["mgr1"], user_ids["mgr2"]), str(winner_owner))
        check("只写了一条归属变更历史（没有两个人各记一条）",
              await history_count(ids["race_cust"]), 1)

        barrier2 = threading.Barrier(2)

        def race2(token: str):
            barrier2.wait()
            return call("POST", f"/public-pool/leads/{ids['race_lead2']}/claim", token)

        results = await asyncio.gather(
            asyncio.to_thread(race2, mgr1_token),
            asyncio.to_thread(race2, mgr2_token),
        )
        codes = sorted(r[0] for r in results)
        check_true("并发领取线索：恰好一个成功",
                   codes.count(200) == 1, f"两个请求分别返回 {codes}")
        check("线索也只写了一条分配历史", await assign_count(ids["race_lead2"]), 1)

        print("=== 7. 收尾核对：越权尝试没有留下任何痕迹 ===")
        check("私有客户仍然一条归属历史都没有（全程未被改动）",
              await history_count(ids["priv_cust"]), 0)
        check("私有线索仍然一条分配历史都没有",
              await assign_count(ids["priv_lead"]), 0)
        check("私有客户负责人自始至终是原主",
              await owner_of(ids["priv_cust"]), user_ids["boss"])

    finally:
        await cleanup()

    print()
    if FAILURES:
        print(f"FAILED {len(FAILURES)} 项：" + "、".join(FAILURES))
        raise SystemExit(1)
    print("OK 公海/线索取数与授权一致、越权被拒、领取有锁且幂等")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
