#!/usr/bin/env python
"""公海回收预告：同一客户**第二轮**预告，原负责人必须再收到一次（返修 R11）。

守的问题
--------
R11：同一客户第二次进入回收预告时，**原负责人收不到通知**。

根因在 `settings/service.py::_notify_recycle_candidate` 的去重键：
`(接收人, 业务类型, 业务对象, 标题)` 四元组。

发给**原负责人**的那条，业务对象用的是 `customer.id` —— 他点进去要落在
「客户详情」（"我的哪个客户要没了"），前端是按 `business_type` 拼跳转地址的
（见 `NotificationBell.tsx::LINK_BY_TYPE`），这个键**不能动**。
可 `customer.id` **跨轮次不变**，于是第二轮的四元组与第一轮完全相同，
去重把第二条通知直接挡掉 —— 客户要被回收了，原负责人却收不到第二次提醒。

发给**复核主管**的那条用的是 `candidate.id`（每轮新候选、id 自然不同），
所以主管那条一直正常。这正是"同一件事、两个人、一个收得到一个收不到"的
由来，也是这个 bug 看着像玄学的原因。

修法：把去重范围收窄到**本轮预告期内** —— 加 `created_at >= candidate.notice_at`。
   · 同一轮重跑 / 补投 → notice_at 不变 → 仍然挡住（不重复打扰，原有行为保住）
   · 下一轮预告 → 新候选 notice_at 更晚 → 上一轮那条落在范围外 → 重新发

本套件守两件事（第 2 条是第 1 条的护栏）
----------------------------------------
1. **第二轮预告，原负责人能收到第二条**（修复前只有 1 条、且静默无感）
2. **同一轮重复触发不会重复发** —— 不能为了修 1 而把去重整个弄没了

跑法（必须显式给 API_BASE 与 DATABASE_URL，指到一次性隔离库）：

    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 \\\\
      DATABASE_URL=postgresql+asyncpg://.../crm_sales_agent_test \\\\
      PYTHONPATH=. .venv/bin/python scripts/check_pool_recycle_notice.py
"""

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

import app.main  # noqa: F401  保证所有模型都注册进 metadata
from sqlalchemy import select, text

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.modules.customer.model import Customer
from app.modules.notification.model import Notification
from app.modules.settings.model import PublicPoolRecycleCandidate, PublicPoolRule
from app.modules.settings.service import _notify_recycle_candidate
from app.modules.user.model import Department, Role, User

FAILURES: list[str] = []
PREFIX = "CHKR11"
STAMP = str(int(time.time()))
#: 专属客户等级：隔离库里只有本套件的客户是这个等级，规则按等级匹配，
#: 所以扫描**只会碰到本套件造的客户**，不会误提名别人的数据。
#: 取值前先查过整个 scripts/ 目录：其它套件用的是 Z（pool/scheduler/task）与
#: Y（sixth_round_p2），所以这里用 **X** 避开。下面的 build_fixtures 还会
#: 再断言一次"该等级名下没有别的客户"，将来有人也挑 X 会立刻炸出来，
#: 而不是悄悄互相污染。
LEVEL = "X"
RULE_DAYS = 30
#: 原负责人那条通知的标题（与 service.py 里的字符串一致）
OWNER_TITLE = "客户回收预告"

BASE = os.environ.get("API_BASE", "")

#: ⚠️ 必须**显式**给 API_BASE，不给就拒跑。
#:
#: 本套件会真的调用「立即扫描公海回收」（POST /public-pool/run-recycle）：
#: 它按等级扫客户、**写候选表、发通知**。一旦忘了传 API_BASE，请求就会打到
#: **开发后端**上（默认 8000），夹具建在隔离库、写入落在开发库，两边对不上
#: 还会污染真数据。（2026-10-06 有套件漏传 API_BASE 打到开发库的前科。）
if not BASE:
    raise SystemExit(
        "必须显式设置 API_BASE（本套件会真的跑回收扫描并写通知，"
        "不能默认打到开发后端 8000）"
    )
if "8000" in BASE:
    raise SystemExit(f"API_BASE 指向 8000（开发后端）很可能是误传：{BASE}")


def check(label: str, actual, expected) -> None:
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: str = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}' + (f"：{detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def call(method: str, path: str, token: str | None = None, body=None):
    data = json.dumps(body).encode() if body is not None else None
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
        except Exception:
            return exc.code, {}


def login(username: str, password: str) -> str:
    _, res = call("POST", "/auth/login", body={"username": username, "password": password})
    if res.get("code") != 0:
        raise SystemExit(f"登录失败（{username}）：{res.get('message')}")
    return res["data"]["access_token"]


IDS: dict = {}


async def cleanup() -> None:
    """自底向上清干净（通知/候选/客户/规则/角色/用户/部门）。"""
    async with SessionLocal() as s:
        owner = IDS.get("owner")
        if owner:
            await s.execute(
                text("delete from notifications where user_id = :u"), {"u": owner}
            )
        await s.execute(
            text(
                "delete from public_pool_recycle_candidates where customer_id in "
                "(select id from customers where name like :p)"
            ),
            {"p": f"{PREFIX}%"},
        )
        await s.execute(text("delete from customers where name like :p"), {"p": f"{PREFIX}%"})
        await s.execute(
            text("delete from public_pool_rules where level = :lv"), {"lv": LEVEL}
        )
        await s.execute(
            text(
                "delete from user_roles where user_id in "
                "(select id from users where username like :u)"
            ),
            {"u": f"{PREFIX.lower()}%"},
        )
        await s.execute(
            text("delete from roles where code like :c"), {"c": f"{PREFIX}%"}
        )
        await s.execute(text("delete from users where username like :u"), {"u": f"{PREFIX.lower()}%"})
        await s.execute(text("delete from departments where name like :d"), {"d": f"{PREFIX}%"})
        await s.commit()


async def build_fixtures() -> None:
    async with SessionLocal() as s:
        # 保险：本套件的规则按等级匹配，若该等级名下已存在**别人的**客户，
        # 扫描会连他们一起提名并发通知（污染别人的夹具、也打乱断言）。
        # 与其事后排查，不如在这里直接说清楚。
        others = (
            await s.execute(
                text(
                    "select count(*) from customers "
                    "where level = :lv and deleted_at is null and name not like :p"
                ),
                {"lv": LEVEL, "p": f"{PREFIX}%"},
            )
        ).scalar()
        if others:
            raise SystemExit(
                f"等级 {LEVEL} 名下已有 {others} 个非本套件客户 —— "
                f"请改用其它等级，否则回收扫描会误伤它们"
            )

        # 先清掉可能残留的专属等级规则，避免两条规则同时命中同一个等级
        await s.execute(text("delete from public_pool_rules where level = :lv"), {"lv": LEVEL})

        dept = Department(name=f"{PREFIX}部-{STAMP}", status="active")
        s.add(dept)
        await s.flush()
        owner = User(
            name=f"{PREFIX}原负责人-{STAMP}",
            username=f"{PREFIX.lower()}_owner_{STAMP}",
            password_hash=hash_password("123456"),
            status="active",
            department_id=dept.id,
        )
        s.add(owner)
        await s.flush()
        role = Role(code=f"{PREFIX}ROLE{STAMP}", name=f"{PREFIX}角色", data_scope="department")
        s.add(role)
        await s.flush()
        c = Customer(
            name=f"{PREFIX}客户-{STAMP}",
            owner_id=owner.id,
            pool_status="private",
            created_by=owner.id,
            level=LEVEL,
            # 100 天没跟进 → 必然超过规则天数（30 天）
            last_followup_at=datetime.now(UTC) - timedelta(days=100),
        )
        s.add(c)
        await s.flush()
        rule = PublicPoolRule(level=LEVEL, days=RULE_DAYS, enabled=True, remark=f"{PREFIX}套件")
        s.add(rule)
        await s.commit()
        IDS.update(owner=owner.id, customer=c.id, dept=dept.id, rule=rule.id)


async def owner_notice_count() -> int:
    """原负责人收到的「客户回收预告」条数。"""
    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(Notification.id).where(
                    Notification.user_id == IDS["owner"],
                    Notification.title == OWNER_TITLE,
                )
            )
        ).scalars().all()
        return len(rows)


async def candidate_of_customer() -> tuple[int, str] | None:
    """本套件客户当前的候选（取最新一条）：返回 (id, status)。"""
    async with SessionLocal() as s:
        row = (
            await s.execute(
                select(PublicPoolRecycleCandidate.id, PublicPoolRecycleCandidate.status)
                .where(PublicPoolRecycleCandidate.customer_id == IDS["customer"])
                .order_by(PublicPoolRecycleCandidate.id.desc())
            )
        ).first()
        return (row[0], row[1]) if row else None


async def close_candidate(candidate_id: int) -> None:
    """把候选置为"已回收"，模拟主管处理完毕 —— 下一轮扫描才能再次提名。

    ⚠️ 只改状态，**不清空客户的 owner_id**：真执行回收会把归属清空，
    而"归属为空"的客户根本不会被扫描提名（扫描要求 owner_id 非空）。
    这里要验的是"第二轮还会不会再发通知"，所以必须让客户保持可被提名。
    """
    async with SessionLocal() as s:
        await s.execute(
            text("update public_pool_recycle_candidates set status = 'executed' where id = :i"),
            {"i": candidate_id},
        )
        await s.commit()


async def main() -> int:
    await cleanup()          # 先清，避免上次中断留下的残渣影响断言
    await build_fixtures()

    token = login("admin", "admin123")
    print(f"夹具：客户 #{IDS['customer']} 等级 {LEVEL}，负责人 #{IDS['owner']}，"
          f"规则 {RULE_DAYS} 天\n")

    # ---------------------------------------------------------------- 第 1 轮
    print("第 1 轮：扫描并生成预告")
    _, res = call("POST", "/public-pool/run-recycle", token=token)
    check("第 1 轮扫描返回 200", res.get("code"), 0)
    after1 = await owner_notice_count()
    check("原负责人收到 1 条预告", after1, 1)

    cand1 = await candidate_of_customer()
    check_true("第 1 轮生成了候选", cand1 is not None, f"{cand1}")
    if cand1 is None:
        await cleanup()
        return 1
    check("第 1 轮候选状态", cand1[1], "pending")

    # 同一轮重复触发：候选还在未结状态，扫描会跳过该客户。
    # 这一条守的是"重跑不会重复打扰"（也顺带证明去重没被改坏）。
    print("\n同一轮重复扫描（候选未结）")
    call("POST", "/public-pool/run-recycle", token=token)
    check("原负责人通知数不增加", await owner_notice_count(), 1)

    # ---------------------------------------------------------------- 第 2 轮
    # 关键：把候选结案，让客户可以再次被提名。
    await close_candidate(cand1[0])
    # 拉开时间：第二轮候选的 notice_at 必须晚于第一轮那条通知的 created_at，
    # 否则"本轮期内"的判据会把两轮混在一起（去重范围就失效了）。
    await asyncio.sleep(1.2)

    print("\n第 2 轮：同一客户再次进入预告（R11 核心）")
    _, res2 = call("POST", "/public-pool/run-recycle", token=token)
    check("第 2 轮扫描返回 200", res2.get("code"), 0)
    after2 = await owner_notice_count()

    # 修复前这里是 1（第二条被去重静默挡掉），修复后应为 2
    check("原负责人**再**收到 1 条（共 2 条）", after2, 2)

    cand2 = await candidate_of_customer()
    check_true("第 2 轮生成了**新**候选", cand2 is not None and cand2[0] != cand1[0],
               f"第1轮 #{cand1[0]} → 第2轮 #{cand2[0] if cand2 else None}")
    if cand2:
        check("第 2 轮候选状态", cand2[1], "pending")

    # ------------------------------------------------- 去重护栏（第 2 条的护栏）
    # 直接对第 2 轮的候选再跑一次发预告：应该被去重挡住（返回 0）。
    # 这一条保证"修 R11 的方式没有把去重整个关掉"。
    print("\n去重护栏：对同一候选重复发预告")
    async with SessionLocal() as s:
        cand_row = await s.get(PublicPoolRecycleCandidate, cand2[0])
        cust_row = await s.get(Customer, IDS["customer"])
        again = await _notify_recycle_candidate(
            s, candidate=cand_row, customer=cust_row, notice_days=7
        )
        await s.commit()
    check("重复调用发出 0 条（被去重）", again, 0)
    check("通知数仍为 2", await owner_notice_count(), 2)

    await cleanup()

    print()
    if FAILURES:
        print(f"✗ 失败 {len(FAILURES)} 项：" + "；".join(FAILURES))
        return 1
    print("✓ 全部通过：第二轮预告原负责人能收到，且同轮不重复发")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
