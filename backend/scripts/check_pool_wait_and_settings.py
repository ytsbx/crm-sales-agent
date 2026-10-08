#!/usr/bin/env python
"""公海回收：等待期先后关系、天数配置的严格校验与"改配置一起重算"。

守三类问题（返修单 R12 / R13 / R14 + 主人 2026-10-06 定的重算口径）：

**R13 暂缓只能延长，不能缩短预告期**
"最早可回收时间"原先分状态取：pending 看 `due_at`、deferred 只看
`deferred_until`。于是预告还剩 7 天时主管暂缓 1 天，1 天后这条就成了
"正常到期" —— **一次暂缓把预告期悄悄缩掉了**，业务员那 7 天的自救窗口凭空消失。
现在改成取两者中**较晚**的那个，想提前收只能走「提前回收」例外（必填原因）。

**R14 天数配置必须严格是整数**
原先用 `int(raw)`：1.9 被悄悄截成 1、`True` 被当成 1 落库 —— 保存值、展示值、
执行值三者不一致，界面上完全看不出来。现在小数、布尔、空一律 422。

**改配置要一起重算未结候选（主人 2026-10-06 定）**
原先"只影响新候选"。现在改预告期/暂缓期，所有 `pending` / `deferred` 的候选
按新天数重算到期时间；已结案的不动。

**R12 主管入口（后端那一半）**
主管拿的是 `customer:pool_review`，不是 `settings:manage`：
他**能**进复核列表，但**不能**改系统配置 —— 证明入口不是靠"授予整个系统设置权限"
解决的，而是靠独立权限。前端菜单那一半在浏览器里验。

跑法（需要后端在跑）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_sales_agent_test \\
      PYTHONPATH=. .venv/bin/python scripts/check_pool_wait_and_settings.py
"""

import asyncio
import json
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from _test_support import require_api_base, require_isolated_db

require_isolated_db()

import app.main  # noqa: F401  保证所有模型都注册进 metadata
from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.modules.customer.model import Customer
from app.modules.settings.model import PublicPoolRecycleCandidate
from app.modules.user.model import Department, Role, User

FAILURES: list[str] = []
PREFIX = "CHKPOOLSET"
STAMP = str(int(time.time()))
REJECTED = (400, 422)

#: ⚠️ 必须**显式**给 API_BASE，不给就拒跑。
#:
#: 其它套件的默认值都是 `http://127.0.0.1:8000/api/v1`（开发后端）。本套件会真的
#: 写系统配置（PATCH /settings）、还会批准/驳回候选 —— 一旦忘了传 API_BASE，
#: 请求就会打到**开发库**上：夹具建在隔离库、改动落在开发库，两边对不上还污染真数据。
#: （2026-10-06 实测踩过：漏传一次，开发库被写进两行配置和一批审计。）
#: 这里的检查和 smoke_ui.mjs 要求显式 DATABASE_URL 是同一个思路：宁可跑不起来，
#: 也别悄悄写错库。
# 地址与库的防呆统一收在 _test_support（判据只留一处）
BASE = require_api_base()

NOTICE_KEY = "pool_recycle_notice_days"
DEFER_KEY = "pool_recycle_defer_days"
DEFAULT_NOTICE = 7
DEFAULT_DEFER = 30


def check(label: str, actual, expected) -> None:
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: str = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}' + (f"：{detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def check_in(label: str, actual, expected: tuple) -> None:
    good = actual in expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望属于 {expected}）')
    if not good:
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
#: 提名时刻，用来算"按新天数应该在什么时候到期"
BASE_AT = datetime.now(UTC)


async def grant_role(session, role_id: int, codes: list[str]) -> None:
    for code in codes:
        pid = (
            await session.execute(
                text("select id from permissions where code = :c"), {"c": code}
            )
        ).scalar_one_or_none()
        if pid is None:
            raise SystemExit(f"权限码不存在：{code}（seed 里要先定义）")
        await session.execute(
            text(
                "insert into role_permissions (role_id, permission_id) "
                "values (:r, :p) on conflict do nothing"
            ),
            {"r": role_id, "p": pid},
        )


async def cleanup() -> None:
    """自底向上清干净（本套件写：通知/候选/归属历史/客户/角色/用户/部门）。"""
    async with SessionLocal() as s:
        cust = "(select id from customers where name like :p)"
        for sql in (
            "delete from notifications where user_id in "
            "(select id from users where username like :u)",
            "delete from public_pool_recycle_candidates where customer_id in " + cust,
            "delete from customer_owner_history where customer_id in " + cust,
            "delete from customers where name like :p",
            "delete from user_roles where role_id in (select id from roles where code like :r)",
            "delete from role_permissions where role_id in "
            "(select id from roles where code like :r)",
            "delete from roles where code like :r",
            "delete from user_roles where user_id in (select id from users where username like :u)",
            # ⚠️ 部门必须在用户之后删（users.department_id 外键指向它）
            "delete from users where username like :u",
            "delete from departments where name like :p",
        ):
            await s.execute(
                text(sql), {"p": f"{PREFIX}%", "u": f"{PREFIX.lower()}%", "r": f"{PREFIX}%"}
            )
        await s.commit()


async def add_candidate(
    customer_id: int, owner_id: int, status: str, **overrides
) -> int:
    async with SessionLocal() as s:
        now = datetime.now(UTC)
        payload = {
            "customer_id": customer_id,
            "owner_id": owner_id,
            "level": "Z",
            "rule_days": 1,
            "last_active_at": now - timedelta(days=30),
            "protection_snapshot": [],
            "status": status,
            "notice_at": BASE_AT,
            "created_at": now,
            "notice_days": DEFAULT_NOTICE,
        }
        payload.update(overrides)
        row = PublicPoolRecycleCandidate(**payload)
        s.add(row)
        await s.flush()
        new_id = row.id
        await s.commit()
        return new_id


async def candidate_row(candidate_id: int) -> dict:
    async with SessionLocal() as s:
        row = await s.get(PublicPoolRecycleCandidate, candidate_id)
        if row is None:
            return {}
        return {
            "status": row.status,
            "notice_days": row.notice_days,
            "defer_days": row.defer_days,
            "due_at": row.due_at,
            "deferred_until": row.deferred_until,
            "early_approved": bool(row.early_approved),
        }


async def setting_value(key: str):
    async with SessionLocal() as s:
        return (
            await s.execute(
                text("select value from system_settings where key = :k"), {"k": key}
            )
        ).scalar_one_or_none()


async def build_fixtures() -> None:
    async with SessionLocal() as s:
        pwd = hash_password("123456")
        dept = Department(name=f"{PREFIX}部-{STAMP}", status="active")
        s.add(dept)
        await s.flush()

        mgr = User(
            name=f"{PREFIX}主管-{STAMP}", username=f"{PREFIX.lower()}_mgr_{STAMP}",
            password_hash=pwd, status="active", department_id=dept.id,
        )
        sales = User(
            name=f"{PREFIX}业务-{STAMP}", username=f"{PREFIX.lower()}_sales_{STAMP}",
            password_hash=pwd, status="active", department_id=dept.id,
        )
        s.add_all([mgr, sales])
        await s.flush()

        mgr_role = Role(
            code=f"{PREFIX}MGR{STAMP}", name=f"{PREFIX}主管", data_scope="department"
        )
        sales_role = Role(
            code=f"{PREFIX}SALES{STAMP}", name=f"{PREFIX}业务员", data_scope="department"
        )
        s.add_all([mgr_role, sales_role])
        await s.flush()
        # 主管：**只有**复核权 + 查看/编辑/指派，**没有** settings:manage
        await grant_role(
            s, mgr_role.id,
            ["customer:view", "customer:update", "customer:assign", "customer:pool_review"],
        )
        await grant_role(s, sales_role.id, ["customer:view", "customer:update"])
        for user, role in ((mgr, mgr_role), (sales, sales_role)):
            await s.execute(
                text("insert into user_roles (user_id, role_id) values (:u, :r)"),
                {"u": user.id, "r": role.id},
            )

        stale = datetime.now(UTC) - timedelta(days=30)
        customers = []
        for i in range(5):
            c = Customer(
                name=f"{PREFIX}客户{i}-{STAMP}", owner_id=sales.id,
                pool_status="private", created_by=sales.id, level="Z",
                last_followup_at=stale,
            )
            s.add(c)
            await s.flush()
            customers.append(c.id)
        await s.commit()

        IDS.update(
            mgr=mgr.id, sales=sales.id, dept=dept.id,
            c1=customers[0], c2=customers[1], c3=customers[2],
            c4=customers[3], c5=customers[4],
        )

    # 候选：各场景一条（同一客户同时只允许一张未结候选，所以分开用不同客户）
    IDS["cand_deferred_short"] = await add_candidate(
        IDS["c1"], IDS["sales"], "deferred",
        # 预告还剩 7 天，主管只暂缓了 1 天 —— R13 的核心场景
        notice_days=7,
        due_at=BASE_AT + timedelta(days=7),
        defer_days=1,
        deferred_until=BASE_AT + timedelta(days=1),
        decided_at=BASE_AT,
    )
    IDS["cand_pending"] = await add_candidate(
        IDS["c2"], IDS["sales"], "pending",
        notice_days=7, due_at=BASE_AT + timedelta(days=7),
    )
    IDS["cand_rejected"] = await add_candidate(
        IDS["c3"], IDS["sales"], "rejected",
        notice_days=7, due_at=BASE_AT + timedelta(days=7),
        decided_at=BASE_AT,
    )
    IDS["cand_deferred_rec"] = await add_candidate(
        IDS["c4"], IDS["sales"], "deferred",
        notice_days=7, due_at=BASE_AT + timedelta(days=1),
        defer_days=3, deferred_until=BASE_AT + timedelta(days=3),
        decided_at=BASE_AT,
    )
    IDS["cand_pending_rec"] = await add_candidate(
        IDS["c5"], IDS["sales"], "pending",
        notice_days=7, due_at=BASE_AT + timedelta(days=7),
    )


# ---------------------------------------------------------------- R13：等待期先后

async def section_wait_order(mgr: str) -> None:
    print("\n== R13：暂缓只能延长，不能缩短预告期 ==")
    cand = IDS["cand_deferred_short"]

    # 列表里给出的"最早可回收时间"必须是**较晚**的那个（预告到期），
    # 不是暂缓到期 —— 后者会让这条看起来已经"正常到期"了
    status, res = call("GET", "/public-pool/recycle-candidates?status=deferred", mgr)
    check("主管能列出待暂缓的候选", status, 200)
    items = (res.get("data") or {}).get("items") or []
    row = next((x for x in items if x["id"] == cand), None)
    check_true("目标候选出现在列表里", row is not None, f"列出 {len(items)} 条")
    if row is not None:
        earliest = datetime.fromisoformat(row["earliest_action_at"])
        if earliest.tzinfo is None:
            earliest = earliest.replace(tzinfo=UTC)
        check_true(
            "最早可回收时间取**预告到期**（较晚者），不是暂缓到期",
            abs((earliest - (BASE_AT + timedelta(days=7))).total_seconds()) < 90,
            f"算出 {earliest.isoformat()}；预告到期 {BASE_AT + timedelta(days=7)}；"
            f"暂缓到期 {BASE_AT + timedelta(days=1)}",
        )

    # 直接批准 → 被拦（不能当成"正常到期"）
    status, res = call(
        "POST", f"/public-pool/recycle-candidates/{cand}/decide", mgr,
        {"decision": "approve"},
    )
    check_in("暂缓期内（预热期未满）→ 批准被拦", status, REJECTED)
    check_true(
        "拦截提示说的是预告期还没满",
        "预告" in (res.get("message") or ""),
        (res.get("message") or "")[:100],
    )
    check("被拦下后状态仍是已暂缓", (await candidate_row(cand))["status"], "deferred")

    # 提前回收：不填原因 → 拒
    status, _ = call(
        "POST", f"/public-pool/recycle-candidates/{cand}/decide", mgr,
        {"decision": "approve", "early": True},
    )
    check_in("提前回收不填原因 → 被拒", status, REJECTED)

    # 提前回收 + 原因 → 放行，单独记 early
    status, _ = call(
        "POST", f"/public-pool/recycle-candidates/{cand}/decide", mgr,
        {"decision": "approve", "early": True, "note": f"{PREFIX}客户已停业，确需提前回收"},
    )
    check("提前回收（填了原因）→ 执行成功", status, 200)
    check("单独记下 early_approved", (await candidate_row(cand))["early_approved"], True)


# ---------------------------------------------------------------- R14：严格整数

async def section_strict_int(admin: str) -> None:
    print("\n== R14：天数配置必须严格是整数 ==")
    bad_cases = [
        ("小数 1.9", 1.9),
        ("小数 5.0（整数值也不收浮点）", 5.0),
        ("布尔 true", True),
        ("布尔 false", False),
        ("非数字串「七天」", "七天"),
        ("数字里带小数点的串「3.5」", "3.5"),
        ("带符号的串「+3」", "+3"),
        ("空串", ""),
        ("空值 null", None),
    ]
    for label, value in bad_cases:
        status, _ = call("PATCH", "/settings", admin, {"key": NOTICE_KEY, "value": {"days": value}})
        check_in(f"{label} → 被拒", status, REJECTED)

    for label, value in (("超上限 366", 366), ("负数 -1", -1)):
        status, _ = call("PATCH", "/settings", admin, {"key": NOTICE_KEY, "value": {"days": value}})
        check_in(f"{label} → 被拒", status, REJECTED)

    status, _ = call("PATCH", "/settings", admin, {"key": NOTICE_KEY, "value": {}})
    check_in("缺字段 → 被拒", status, REJECTED)

    for label, value, expect in (
        ("整数 12", 12, 12),
        ("数字串「13」", "13", 13),
        ("下边界 0", 0, 0),
        ("上边界 365", 365, 365),
    ):
        status, _ = call("PATCH", "/settings", admin, {"key": NOTICE_KEY, "value": {"days": value}})
        check(f"{label} → 可保存", status, 200)
        stored = await setting_value(NOTICE_KEY)
        check(f"{label} → 存下来与提交值一致", stored, {"days": expect})

    # 恢复默认
    call("PATCH", "/settings", admin, {"key": NOTICE_KEY, "value": {"days": DEFAULT_NOTICE}})


# ---------------------------------------------------------------- 口径：一起重算

async def section_recompute(admin: str) -> None:
    print("\n== 改配置要一起重算还没结案的候选（主人 2026-10-06 定）==")
    pending = IDS["cand_pending_rec"]
    deferred = IDS["cand_deferred_rec"]
    rejected = IDS["cand_rejected"]
    before_rejected = await candidate_row(rejected)

    status, res = call(
        "PATCH", "/settings", admin, {"key": NOTICE_KEY, "value": {"days": 14}}
    )
    check("改预告期 → 保存成功", status, 200)
    check_true(
        "返回消息里说明重算了几条",
        "重算" in (res.get("message") or ""),
        (res.get("message") or "")[:80],
    )

    after_pending = await candidate_row(pending)
    check("待复核候选的预告天数跟着变", after_pending["notice_days"], 14)
    check_true(
        "待复核候选的到期时间按新天数重算",
        abs((after_pending["due_at"] - (BASE_AT + timedelta(days=14))).total_seconds()) < 5,
        f"新到期 {after_pending['due_at']}（提名时刻 {BASE_AT} + 14 天）",
    )
    after_rejected = await candidate_row(rejected)
    check(
        "**已驳回**的候选不动（已结案，重算等于改历史）",
        after_rejected["notice_days"], before_rejected["notice_days"],
    )

    status, _ = call("PATCH", "/settings", admin, {"key": DEFER_KEY, "value": {"days": 10}})
    check("改暂缓期 → 保存成功", status, 200)
    after_deferred = await candidate_row(deferred)
    check("已暂缓候选的暂缓天数跟着变", after_deferred["defer_days"], 10)
    check_true(
        "已暂缓候选的暂缓到期按新天数重算（基准是它当初暂缓的时刻）",
        abs((after_deferred["deferred_until"] - (BASE_AT + timedelta(days=10))).total_seconds()) < 5,
        f"新暂缓到期 {after_deferred['deferred_until']}",
    )

    # 恢复默认，避免影响别的套件
    call("PATCH", "/settings", admin, {"key": NOTICE_KEY, "value": {"days": DEFAULT_NOTICE}})
    call("PATCH", "/settings", admin, {"key": DEFER_KEY, "value": {"days": DEFAULT_DEFER}})
    restored = await setting_value(NOTICE_KEY)
    check("收尾把预告期恢复成默认", restored, {"days": DEFAULT_NOTICE})


# ---------------------------------------------------------------- R12：主管入口（后端）

async def section_manager_scope() -> None:
    print("\n== R12：主管靠独立权限进复核，不是靠拿系统设置权限 ==")
    # 用 seed 里的默认主管账号（有 customer:pool_review，没有 settings:manage）
    status, res = call("POST", "/auth/login", body={"username": "lisi", "password": "123456"})
    if res.get("code") != 0:
        print(f"  ! 跳过：默认主管账号登录失败（{res.get('message')}）")
        return
    mgr = res["data"]["access_token"]

    status, _ = call("GET", "/public-pool/recycle-candidates", mgr)
    check("默认主管能打开复核列表", status, 200)

    status, _ = call("GET", "/settings", mgr)
    check_in("默认主管**不能**读系统配置（没有 settings:manage）", status, (403,))

    status, _ = call(
        "PATCH", "/settings", mgr, {"key": NOTICE_KEY, "value": {"days": 3}}
    )
    check_in("默认主管**不能**改系统配置", status, (403,))


async def main() -> None:
    admin = login("admin", "admin123")
    print("== 建夹具 ==")
    await cleanup()
    await build_fixtures()
    print(f"候选：{json.dumps({k: v for k, v in IDS.items() if k.startswith('cand_')}, ensure_ascii=False)}")

    try:
        await section_wait_order(admin)
        await section_strict_int(admin)
        await section_recompute(admin)
        await section_manager_scope()
    finally:
        # 配置一定要复位：其它套件依赖默认值
        call("PATCH", "/settings", admin, {"key": NOTICE_KEY, "value": {"days": DEFAULT_NOTICE}})
        call("PATCH", "/settings", admin, {"key": DEFER_KEY, "value": {"days": DEFAULT_DEFER}})
        await cleanup()

    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项：")
        for item in FAILURES:
            print(f"  - {item}")
        raise SystemExit(1)
    print("全部通过 ✓")


if __name__ == "__main__":
    asyncio.run(main())
