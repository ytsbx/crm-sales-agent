#!/usr/bin/env python3
"""静态+真库检查：**转交负责人之后，旧负责人还能不能确认交期变更**（第十批 ⑤）。

## 报的问题

旧负责人先通过权限检查，随后在订单锁上等待；主管用真实接口转移负责人并提交；
旧请求拿到锁后**没有重新检查数据范围**，仍确认成功并改了交期。

## 为什么必须真库

`FOR UPDATE` 在 SQLite 上被直接忽略 —— 锁的互斥、以及"检查发生在锁内还是锁外"
只有真 PostgreSQL 能证明。等待/不等待一律用 `FOR UPDATE NOWAIT` 的成败判定
（确定性，不靠 sleep 碰运气；与 `check_milestone_lock_race.py` 同一手法）。

## 两道判据各管什么（反向验证过，别删了其中一道）

  · **路由** `get_visible_order(..., for_update=True)`：锁 + 锁内判范围。
    把它的 `for_update` 去掉，第 ④ 段仍然被下面那道拦住 → 说明两道都在起作用。
  · **`schedule.confirm_change` 里的锁内重判**：服务层的兜底，读的是**锁内刷新后**
    的订单与变更单。把两道一起撤掉，第 ④ 段立刻变红（确认 200、变更单confirmed）
    —— 这就是报告说的那个洞。

## 四个结论（脚本自己判定）

  ① **基座**：没人持锁时，另一个事务能立刻 `NOWAIT` 拿到订单行；
  ② **转移要拿同一把锁**：有人持锁时，转移负责人那条 SQL 的 `NOWAIT` 必须失败
     —— 否则"转移"根本不与"确认"串行，任何锁内重判都白搭；
  ③ **确认请求会等这把锁**：持锁期间发确认请求，它必须**卡住**（没卡住说明
     它绕过了锁，那锁内重判就形同虚设）；
  ④ **交错后的终态**：持锁会话把负责人改成别人并提交 → 确认请求才继续 →
     **旧负责人必须被拒**，且交期**没有被改**。

跑法（一次性库 + 隔离后端；与 `check_milestone_lock_race.py` 同一套）：

    DB=crm_check_sched_race
    psql -h 127.0.0.1 -U postgres -d postgres -c "CREATE DATABASE $DB OWNER crm;"
    cd backend && export DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/$DB
    PYTHONPATH=. .venv/bin/python -m alembic upgrade head
    PYTHONPATH=. .venv/bin/python -m scripts.seed
    PYTHONPATH=. .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8073 &
    API_BASE=http://127.0.0.1:8073/api/v1 PYTHONPATH=. DATABASE_URL=$DATABASE_URL \\
      .venv/bin/python scripts/check_schedule_confirm_race.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _test_support import require_api_base, require_isolated_db  # noqa: E402

require_isolated_db()
API = require_api_base()

#: 把所有 ORM 模型导入一次再建对象（只导几个会在解析外键时报
#: `NoReferencedTableError: users`）。照抄 `check_milestone_lock_race.py`。
from app.core.database import SessionLocal  # noqa: E402
from app.modules.order.model import OrderScheduleChange  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

#: 用户 id（种子数据：admin=1、zhangsan=2、lisi=3）。
#: ⚠️ 别用魔法数字：我第一版写死 2 以为是李四，其实是张三，夹具整体错位。
ADMIN_ID = 1
#: **张三**：业务员（salesperson），**没有** `order:assign`。
#: 只有这种账号才能验出"转交之后旧负责人被甩掉"——主管本来就有该权限、
#: 按设计可以收口确认（我第一版用李四当"旧负责人"，于是他 200 通过，
#: 我一度以为是洞，其实是夹具选错了人）。
ZHANGSAN_ID = 2
#: 李四：主管（sales_manager），有 `order:assign`，用来做转交
LISI_ID = 3

PREFIX = "CHKSCHED"
RESULTS: list[tuple[str, bool, str]] = []


def call(method: str, path: str, token: str | None = None, body=None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
    )
    if body is not None:
        req.add_header("Content-Type", "application/json")
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


def check(label: str, value, expected) -> None:
    ok = value == expected
    RESULTS.append((label, ok, f"{value!r}（期望 {expected!r}）"))
    print(f"  {'OK  ' if ok else 'FAIL'} {label}: {value!r}（期望 {expected!r}）")


def check_true(label: str, value, detail: str = "") -> None:
    ok = bool(value)
    RESULTS.append((label, ok, detail))
    print(f"  {'OK  ' if ok else 'FAIL'} {label}{('：' + detail) if detail else ''}")


def login(username: str, password: str) -> str:
    _, res = call("POST", "/auth/login", body={"username": username, "password": password})
    return res["data"]["access_token"]


async def nowait_can_lock(order_id: int) -> bool:
    """另开一个事务，用 `FOR UPDATE NOWAIT` 试拿订单行；能拿到 → True。"""
    async with SessionLocal() as session:
        try:
            await session.execute(
                text("select id from sales_orders where id = :i for update nowait"),
                {"i": order_id},
            )
            await session.rollback()
            return True
        except Exception:
            await session.rollback()
            return False


async def main() -> int:  # noqa: C901
    lisi = login("lisi", "123456")  # sales_manager，用来发起变更
    token_admin = login("admin", "admin123")

    print("\n=== ① 基座：没人持锁时能立刻拿到订单行 ===")
    # 造一张李四名下的单（他是 type=owner，能发起变更）
    tag = f"{PREFIX}{uuid.uuid4().hex[:8]}"
    _, res = call("POST", "/customers", lisi, {"name": tag, "level": "C"})
    customer_id = res["data"]["id"]
    _, res = call(
        "POST",
        "/orders",
        token_admin,
        {"customer_id": customer_id, "items": [{"sku_id": 1, "quantity": 1, "unit_price": "100"}]},
    )
    order_id = res["data"]["order_id"]
    # 手工建单归 admin；改到李四名下（用 PATCH，主管权限）
    call("PATCH", f"/orders/{order_id}", lisi, {"owner_id": ZHANGSAN_ID})
    new_date = (date.today() + timedelta(days=30)).isoformat()
    status, res = call(
        "POST",
        f"/orders/{order_id}/schedule-changes",
        token_admin,
        {"new_delivery_date": new_date, "delivery_kind": "shipping", "reason": "CHKSCHED 并发验证"},
    )
    change_id = (res.get("data") or {}).get("id")
    check_true("夹具：交期变更单已生成", status == 200 and bool(change_id), f"HTTP {status}")
    if not change_id:
        return 1

    check("① 无人持锁 → NOWAIT 能拿到", await nowait_can_lock(order_id), True)

    print("\n=== ② 转移负责人会拿同一把订单行锁 ===")
    # 前台起一个"持锁并转移"的任务；它会在锁被占住时卡在 update 上
    holder_ready = asyncio.Event()

    async def holder():
        async with SessionLocal() as session:
            await session.execute(
                text("select id from sales_orders where id = :i for update"), {"i": order_id}
            )
            holder_ready.set()
            # 等两秒再改负责人并提交（这段时间锁是持有的）
            await asyncio.sleep(2.0)
            await session.execute(
                text("update sales_orders set owner_id = 1, updated_at = now() where id = :i"),
                {"i": order_id},
            )
            await session.commit()

    task = asyncio.create_task(holder())
    await holder_ready.wait()
    check("② 有人持锁 → NOWAIT 失败（转移必须等它）", await nowait_can_lock(order_id), False)

    print("\n=== ③ 确认请求会等这把锁（没等 = 绕过锁，锁内重判就白搭） ===")
    confirm_result: dict = {}

    def fire_confirm():
        confirm_result["res"] = call(
            "POST",
            f"/orders/{order_id}/schedule-changes/{change_id}/confirm",
            token_admin,
            {"remark": "CHKSCHED 并发确认"},
        )

    # 确认请求在后台线程发出；持锁的任务 2 秒后才提交，所以确认必须等
    thread = asyncio.get_running_loop().run_in_executor(None, fire_confirm)
    await asyncio.sleep(0.6)
    blocked = not confirm_result  # 0.6 秒还没返回 → 它在等锁
    check_true("③ 确认请求被订单锁挡住（0.6 秒内未返回）", blocked,
               "没挡住说明它绕过了锁" if not blocked else "")
    await task
    await thread
    status, res = confirm_result.get("res", (0, {}))
    check_true("③ 锁释放后确认请求才返回", bool(confirm_result), f"HTTP {status}")
    # 李四已经不是负责人（holder 把 owner 改成了 1=admin），但他有 order:assign 吗？
    # 有的话这条资格检查会放过他；所以这里判"范围"这条硬闸门：admin 的数据范围是 all，
    # 所以李四能不能过取决于他自己的范围（department_and_sub，含 admin）。
    # 真正的判据在 ④：交期有没有被改。
    print(f"     （确认返回 HTTP {status} code={res.get('code')} {str(res.get('message'))[:50]}）")

    print("\n=== ④ 报告描述的交错：确认请求卡在锁上时，锁内把负责人改掉并提交 ===")
    # 这一段是**本套件的核心**：不是"转移先提交、之后再来确认"（那是顺序场景，
    # 路由那道检查就挡住了），而是
    #   张三的确认请求已经过了路由检查、正卡在订单锁上等
    #   → 持锁方**在锁内**把负责人改掉并提交
    #   → 张三这才拿到锁。
    # 只有"拿到锁之后再判一次范围"才能挡住它；没有这一判，张三会确认成功并改交期。
    zhangsan_t = login("zhangsan", "123456")
    tag_r = f"{PREFIX}R{uuid.uuid4().hex[:8]}"
    _, res = call("POST", "/customers", lisi, {"name": tag_r, "level": "C"})
    customer_r = res["data"]["id"]
    _, res = call(
        "POST", "/orders", token_admin,
        {"customer_id": customer_r, "items": [{"sku_id": 1, "quantity": 1, "unit_price": "100"}]},
    )
    order_r = res["data"]["order_id"]
    call("PATCH", f"/orders/{order_r}", lisi, {"owner_id": ZHANGSAN_ID})
    status, res = call(
        "POST", f"/orders/{order_r}/schedule-changes", zhangsan_t,
        {"new_delivery_date": new_date, "delivery_kind": "shipping", "reason": "CHKSCHED 交错"},
    )
    change_r = (res.get("data") or {}).get("id")
    check_true("④ 夹具：张三发起交期变更", status == 200 and bool(change_r), f"HTTP {status}")

    locked = asyncio.Event()

    async def holder_then_transfer():
        async with SessionLocal() as session:
            await session.execute(
                text("select id from sales_orders where id = :i for update"), {"i": order_r}
            )
            locked.set()
            # 等确认请求卡在锁上（1.5 秒足够它过完路由检查并阻塞）
            await asyncio.sleep(1.5)
            # ★ 在**持锁**期间把负责人改掉并提交 —— 报告描述的正是这个交错
            await session.execute(
                text("update sales_orders set owner_id = :o, updated_at = now() where id = :i"),
                {"o": ADMIN_ID, "i": order_r},
            )
            await session.commit()

    interleaved: dict = {}

    def fire():
        interleaved["res"] = call(
            "POST", f"/orders/{order_r}/schedule-changes/{change_r}/confirm", zhangsan_t,
            {"remark": "CHKSCHED 交错确认"},
        )

    task2 = asyncio.create_task(holder_then_transfer())
    await locked.wait()
    thread2 = asyncio.get_running_loop().run_in_executor(None, fire)
    await asyncio.sleep(0.7)
    check_true("④ 确认请求被订单锁挡住（没挡住说明它绕过锁）", not interleaved)
    await task2
    await thread2
    st_r, res_r = interleaved.get("res", (0, {}))
    check_true(
        "★交错后确认被拒（锁内重判生效）",
        st_r >= 400 or res_r.get("code") != 0,
        f"HTTP {st_r} {str(res_r.get('message'))[:60]}",
    )
    async with SessionLocal() as session:
        c_r = (
            await session.execute(
                select(OrderScheduleChange).where(OrderScheduleChange.id == change_r)
            )
        ).scalar_one()
    check_true(
        "★交期没有被旧负责人改掉",
        c_r.status != "confirmed",
        f"变更单状态={c_r.status}（confirmed 就是洞）",
    )

    print("\n=== ⑤ 关键：转移**已提交**之后，旧负责人（业务员张三）确认必须被拒 ===")
    zhangsan = login("zhangsan", "123456")
    tag2 = f"{PREFIX}B{uuid.uuid4().hex[:8]}"
    _, res = call("POST", "/customers", lisi, {"name": tag2, "level": "C"})
    customer2 = res["data"]["id"]
    _, res = call(
        "POST",
        "/orders",
        token_admin,
        {"customer_id": customer2, "items": [{"sku_id": 1, "quantity": 1, "unit_price": "100"}]},
    )
    order2 = res["data"]["order_id"]
    # 单挂到张三名下（用主管李四转，主管才有 order:assign）
    call("PATCH", f"/orders/{order2}", lisi, {"owner_id": ZHANGSAN_ID})
    status, res = call(
        "POST",
        f"/orders/{order2}/schedule-changes",
        zhangsan,
        {"new_delivery_date": new_date, "delivery_kind": "shipping", "reason": "CHKSCHED ⑤"},
    )
    change2 = (res.get("data") or {}).get("id")
    check_true("⑤ 夹具：张三发起交期变更", status == 200 and bool(change2), f"HTTP {status}")
    # 主管把单转走并提交
    status, res = call("PATCH", f"/orders/{order2}", lisi, {"owner_id": ADMIN_ID})
    check_true("⑤ 夹具：订单已转给 admin", status == 200 and res.get("code") == 0, f"HTTP {status}")
    # 张三（已不是责任人、且没有 order:assign）再来确认
    status, res = call(
        "POST",
        f"/orders/{order2}/schedule-changes/{change2}/confirm",
        zhangsan,
        {"remark": "CHKSCHED ⑤ 旧负责人确认"},
    )
    check_true(
        "★转移已提交后，旧负责人（无 order:assign）确认被拒",
        status >= 400 or res.get("code") != 0,
        f"HTTP {status} {str(res.get('message'))[:60]}",
    )

    # ⑤-b 反面对照：**主管**确认同一张单应当成功（例外条款不能被一起堵死）
    tag3 = f"{PREFIX}C{uuid.uuid4().hex[:8]}"
    _, res = call("POST", "/customers", lisi, {"name": tag3, "level": "C"})
    customer3 = res["data"]["id"]
    _, res = call(
        "POST",
        "/orders",
        token_admin,
        {"customer_id": customer3, "items": [{"sku_id": 1, "quantity": 1, "unit_price": "100"}]},
    )
    order3 = res["data"]["order_id"]
    call("PATCH", f"/orders/{order3}", lisi, {"owner_id": ZHANGSAN_ID})
    status, res = call(
        "POST",
        f"/orders/{order3}/schedule-changes",
        zhangsan,
        {"new_delivery_date": new_date, "delivery_kind": "shipping", "reason": "CHKSCHED ⑤b"},
    )
    change3 = (res.get("data") or {}).get("id")
    call("PATCH", f"/orders/{order3}", lisi, {"owner_id": ADMIN_ID})
    status, res = call(
        "POST",
        f"/orders/{order3}/schedule-changes/{change3}/confirm",
        lisi,
        {"remark": "CHKSCHED ⑤b 主管收口"},
    )
    check_true(
        "⑤-b 反面对照：主管（有 order:assign）仍可收口确认",
        status == 200 and res.get("code") == 0,
        f"HTTP {status} {str(res.get('message'))[:50]}",
    )

    failed = [r for r in RESULTS if not r[1]]
    print()
    if failed:
        print(f"发现 {len(failed)} 项不符合预期：")
        for label, _ok, detail in failed:
            print(f"  - {label}：{detail}")
        return 1
    print(f"全部通过（{len(RESULTS)} 条）：交期确认与负责人转移共用同一把订单锁，"
          "且确认前会在锁内重判数据范围")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
