"""第六批返修 P2（8~11）+ 追加口径（1/2/3）回归。

**只在隔离库跑**：库名必须含 test（或 CI=true），且推送开关全关。

## 这一批修的是什么（每条对应审查方的验收要求）

**8 预告与等待期**
此前扫描只落一条候选记录、**一条预告都不发**；候选上存了 `due_at`
（预告到期）和 `deferred_until`（暂缓到期），但批准时根本不看 ——
"预告 7 天""暂缓 30 天"当天就能破。
现在：① 提名后给原负责人与复核主管各发一条预告；② 批准前校验等待期，
没满 422 并给出最早可回收时间；③ 提前回收是独立例外动作（必填原因 + 单独审计）。
验收：**未到期被拦 / 提前回收要原因 / 暂缓期内能驳回不能收 / 通知双方且去重**。

**追加口径 1 参数可配**
预告期与暂缓期做成配置，后端校验合法性、审计记前后值。
⚠️ 口径已于 2026-10-06 变更：原设计是"每条候选保存生成时的天数、改配置不追溯
旧候选"；主人改成**一起重算** —— 改天数时把还没结案的候选（pending/deferred）
按新天数重新算到期时间。本套件对应的断言已跟着翻面。
验收：**非法值被拒（含小数/布尔，R14）/ 审计有 before / 改配置后旧候选期限跟着重算**。

**追加口径 2 恢复权限**
恢复改读配置项 `pool_recycle_restore_permission`（此前只在默认值表里躺着），
并按客户数据范围过滤。
验收：**主管能恢复本团队、业务员不能、被领走报冲突**。

**9 打样修订链**
V1 被 V2 替代后，V1 仍挂着"在途打样"保护 —— 而 V1 早已冻结、改都改不了，
客户因此永远进不了回收。现在被替代的历史版本不再独立保护。
验收：**V1→V2 / V1→V2→V3 / 同客户另一张独立打样**。

**10 批量接口与页面流程**
批量复核此前 id 走查询参数、决定走请求体，与前端正好反着，从未调通；
页面只查 pending，"暂缓"后记录消失、已回收的没有恢复入口。
验收：**统一请求体可调通 / 各种状态都能列出来**。

跑法（需要后端在跑）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_sales_agent_test \\
      PYTHONPATH=. .venv/bin/python scripts/check_sixth_round_p2.py
"""

import asyncio
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

import app.main  # noqa: F401  保证所有模型都注册进 metadata
from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.modules.customer.model import Customer
from app.modules.sample.model import SampleRequest
from app.modules.settings import service as settings_service
from app.modules.settings.model import PublicPoolRecycleCandidate, PublicPoolRule
from app.modules.user.model import Department, Role, User

FAILURES: list[str] = []
BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000/api/v1")
PREFIX = "CHK6P2"
STAMP = str(int(time.time()))
REJECTED = (400, 422)
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
    """自底向上清干净（本套件写：通知/候选/打样/客户/规则/用户角色/部门）。"""
    async with SessionLocal() as s:
        cust = "(select id from customers where name like :p)"
        for sql in (
            # 预告通知：business_id 是裸 BIGINT，没有外键，必须显式删，
            # 否则会留在通知中心里影响守门套件的残留检查
            "delete from notifications where user_id in "
            "(select id from users where username like :u)",
            "delete from public_pool_recycle_candidates where customer_id in " + cust,
            "delete from customer_owner_history where customer_id in " + cust,
            "delete from sample_requests where customer_id in " + cust,
            "delete from customers where name like :p",
            "delete from public_pool_rules where remark like :p",
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


IDS: dict = {}


async def build_fixtures() -> None:
    async with SessionLocal() as s:
        pwd = hash_password("123456")
        dept_a = Department(name=f"{PREFIX}甲部-{STAMP}", status="active")
        dept_b = Department(name=f"{PREFIX}乙部-{STAMP}", status="active")
        s.add_all([dept_a, dept_b])
        await s.flush()

        def make_user(tag: str, dept_id: int | None = None) -> User:
            return User(
                name=f"{PREFIX}{tag}-{STAMP}",
                username=f"{PREFIX.lower()}_{tag}_{STAMP}",
                password_hash=pwd,
                status="active",
                department_id=dept_id,
            )

        mgr_a = make_user("mgra", dept_a.id)
        mgr_b = make_user("mgrb", dept_b.id)
        sales_a = make_user("salesa", dept_a.id)
        sales_b = make_user("salesb", dept_b.id)
        s.add_all([mgr_a, mgr_b, sales_a, sales_b])
        await s.flush()
        IDS.update(
            mgr_a=mgr_a.id, mgr_b=mgr_b.id, sales_a=sales_a.id, sales_b=sales_b.id,
            dept_a=dept_a.id, dept_b=dept_b.id,
        )

        mgr_role = Role(
            code=f"{PREFIX}MGR{STAMP}", name=f"{PREFIX}主管", data_scope="department"
        )
        sales_role = Role(
            code=f"{PREFIX}SALES{STAMP}", name=f"{PREFIX}业务员", data_scope="department"
        )
        s.add_all([mgr_role, sales_role])
        await s.flush()
        await grant_role(
            s, mgr_role.id,
            ["customer:view", "customer:update", "customer:assign", "customer:pool_review"],
        )
        # 业务员：有查看与编辑，**没有** pool_review、也**没有** customer:assign
        await grant_role(s, sales_role.id, ["customer:view", "customer:update"])
        for user, role in (
            (mgr_a, mgr_role), (mgr_b, mgr_role),
            (sales_a, sales_role), (sales_b, sales_role),
        ):
            await s.execute(
                text("insert into user_roles (user_id, role_id) values (:u, :r)"),
                {"u": user.id, "r": role.id},
            )

        # 客户：甲部两名业务员名下各一个（都不带任何履约事项 → 不被保护）
        stale = datetime.now(UTC) - timedelta(days=30)
        c1 = Customer(
            name=f"{PREFIX}甲部客户-{STAMP}", owner_id=sales_a.id,
            pool_status="private", created_by=sales_a.id, level="Z",
            last_followup_at=stale,
        )
        c2 = Customer(
            name=f"{PREFIX}甲部客户2-{STAMP}", owner_id=sales_a.id,
            pool_status="private", created_by=sales_a.id, level="Z",
            last_followup_at=stale,
        )
        # 打样链用的客户（不参与扫描：给个不会命中的等级）
        c3 = Customer(
            name=f"{PREFIX}打样客户-{STAMP}", owner_id=sales_a.id,
            pool_status="private", created_by=sales_a.id, level="Y",
            last_followup_at=datetime.now(UTC),  # 活跃 → 不会被提名
        )
        # 暂缓场景专用（不能和上面两个客户共用：同一客户同时只允许一张未结候选）
        c4 = Customer(
            name=f"{PREFIX}暂缓客户-{STAMP}", owner_id=sales_a.id,
            pool_status="private", created_by=sales_a.id, level="Y",
            last_followup_at=datetime.now(UTC),
        )
        s.add_all([c1, c2, c3, c4])
        # 专供本套件命中的回收规则（等级 Z，1 天未跟进）
        s.add(
            PublicPoolRule(
                level="Z", days=1, enabled=True, remark=f"{PREFIX}规则-{STAMP}"
            )
        )
        await s.flush()
        IDS.update(c1=c1.id, c2=c2.id, c3=c3.id, c4=c4.id)

        # ---- 第 8 条：等待期的三类候选（直接造，便于控制到期时间） ----
        now = datetime.now(UTC)

        async def add_candidate(customer_id: int, owner_id: int, status: str, **kw) -> int:
            row = PublicPoolRecycleCandidate(
                customer_id=customer_id, owner_id=owner_id, level="Z", rule_days=1,
                last_active_at=now - timedelta(days=30),
                protection_snapshot=[], status=status,
                notice_at=now, created_at=now, **kw
            )
            s.add(row)
            await s.flush()
            return row.id

        # ① 预告期还没满（3 天后才到期）
        IDS["cand_pending_future"] = await add_candidate(
            c1.id, sales_a.id, "pending", notice_days=7,
            due_at=now + timedelta(days=3),
        )
        # ② 预告期已过（可以正常回收）—— 客户用另一个，避免与①抢唯一索引
        IDS["cand_pending_due"] = await add_candidate(
            c2.id, sales_a.id, "pending", notice_days=7,
            due_at=now - timedelta(days=1),
        )
        await s.commit()


async def build_sample_chain() -> None:
    """第 9 条：打样修订链。直接造行 —— 保护判据只读 status/confirm_status/parent_id。"""
    async with SessionLocal() as s:
        c3 = IDS["c3"]
        now = datetime.now(UTC)

        def make_sample(**kw) -> SampleRequest:
            return SampleRequest(
                customer_id=c3, owner_id=IDS["sales_a"], requested_at=now,
                created_by=IDS["sales_a"], **kw
            )

        # 链 A：V1 已签收未通过（单独存在时会保护）→ V2 已接受（链到此为止）
        v1 = make_sample(status="signed", confirm_status="pending", version=1)
        s.add(v1)
        await s.flush()
        v2 = make_sample(
            status="signed", confirm_status="accepted", version=2, parent_id=v1.id
        )
        s.add(v2)
        await s.flush()
        IDS.update(chain_a_v1=v1.id, chain_a_v2=v2.id)

        # 链 B：V1 → V2 → V3，V3 还没做完（应继续保护）
        b1 = make_sample(status="signed", confirm_status="rejected", version=1)
        s.add(b1)
        await s.flush()
        b2 = make_sample(status="signed", confirm_status="rejected", version=2, parent_id=b1.id)
        s.add(b2)
        await s.flush()
        b3 = make_sample(status="pending", confirm_status="pending", version=3, parent_id=b2.id)
        s.add(b3)
        await s.flush()
        IDS.update(chain_b_v3=b3.id)

        # 链 C：同客户另有一张**独立**打样（不在链上，自己保护）
        solo = make_sample(status="pending", confirm_status="pending", version=1)
        s.add(solo)
        await s.flush()
        IDS["solo"] = solo.id
        await s.commit()


async def owner_of(customer_id: int) -> int | None:
    async with SessionLocal() as s:
        row = await s.get(Customer, customer_id)
        return row.owner_id if row else None


async def candidate_row(candidate_id: int) -> dict:
    async with SessionLocal() as s:
        row = await s.get(PublicPoolRecycleCandidate, candidate_id)
        if row is None:
            return {}
        return {
            "status": row.status,
            "notice_days": row.notice_days,
            "defer_days": row.defer_days,
            "due_at": row.due_at.isoformat() if row.due_at else None,
            "deferred_until": row.deferred_until.isoformat() if row.deferred_until else None,
            "early_approved": bool(row.early_approved),
            "exception_approved": bool(row.exception_approved),
        }


# ---------------------------------------------------------------- 第 8 条：等待期

async def section1_deadlines(mgr_a: str) -> None:
    print("\n== 第 8 条：预告期/暂缓期真的生效 ==")
    future = IDS["cand_pending_future"]

    # ① 预告期没满 → 普通批准被拦
    status, res = call(
        "POST", f"/public-pool/recycle-candidates/{future}/decide", mgr_a,
        {"decision": "approve"},
    )
    check_in("预告期未满 → 批准回收被拦", status, REJECTED)
    check_true(
        "拦截提示里给出最早可回收时间",
        "最早" in (res.get("message") or ""),
        (res.get("message") or "")[:80],
    )
    check("被拦下后状态仍是待复核", (await candidate_row(future))["status"], "pending")

    # ② 提前回收：不填原因 → 拒
    status, _ = call(
        "POST", f"/public-pool/recycle-candidates/{future}/decide", mgr_a,
        {"decision": "approve", "early": True},
    )
    check_in("提前回收不填原因 → 被拒", status, REJECTED)

    # ③ 提前回收 + 填原因 → 放行，且**单独**记 early_approved
    status, res = call(
        "POST", f"/public-pool/recycle-candidates/{future}/decide", mgr_a,
        {"decision": "approve", "early": True, "note": f"{PREFIX}确需立刻回收"},
    )
    check("提前回收（填了原因）→ 执行成功", status, 200)
    row = await candidate_row(future)
    check("单记 early_approved", row["early_approved"], True)
    check("没有履约保护 → 不误记 exception_approved", row["exception_approved"], False)
    check("客户已进公海（归属被清空）", await owner_of(IDS["c1"]), None)

    # ④ 暂缓期内：能驳回、不能收
    async with SessionLocal() as s:
        now = datetime.now(UTC)
        row = PublicPoolRecycleCandidate(
            customer_id=IDS["c4"], owner_id=IDS["sales_a"], level="Z", rule_days=1,
            last_active_at=now - timedelta(days=30), protection_snapshot=[],
            status="deferred", notice_at=now, created_at=now,
            notice_days=7, defer_days=30, deferred_until=now + timedelta(days=20),
            decided_at=now,
        )
        s.add(row)
        await s.flush()
        deferred_id = row.id
        await s.commit()
    IDS["cand_deferred"] = deferred_id

    status, res = call(
        "POST", f"/public-pool/recycle-candidates/{deferred_id}/decide", mgr_a,
        {"decision": "approve"},
    )
    check_in("暂缓期内 → 批准回收被拦", status, REJECTED)
    check_true(
        "提示里说明是暂缓期",
        "暂缓" in (res.get("message") or ""),
        (res.get("message") or "")[:80],
    )
    # 已过期的暂缓单（另造一张）走不了「驳回」，但**正在暂缓期内**这张可以驳回
    status, _ = call(
        "POST", f"/public-pool/recycle-candidates/{deferred_id}/decide", mgr_a,
        {"decision": "reject", "note": f"{PREFIX}客户自己回来了"},
    )
    check("暂缓期内 → 驳回随时可以做", status, 200)
    check("驳回后状态是已驳回", (await candidate_row(deferred_id))["status"], "rejected")

    # ⑤ 到期后正常批准（不需要 early）
    due_id = IDS["cand_pending_due"]
    status, _ = call(
        "POST", f"/public-pool/recycle-candidates/{due_id}/decide", mgr_a,
        {"decision": "approve"},
    )
    check("预告期已过 → 普通批准即可（不用走例外）", status, 200)
    check("这次没记 early_approved", (await candidate_row(due_id))["early_approved"], False)


# ---------------------------------------------------------------- 第 8 条：通知

async def section2_notice(admin: str, mgr_a: str, mgr_b: str, sales_a_t: str) -> None:
    print("\n== 第 8 条：预告真的发出去（原负责人 + 范围内主管）==")
    # 专门造一个"私海 + 久未跟进"的客户供本轮扫描命中：
    # 前面 section1 已经把 c1/c2 收进了公海、把 c4 驳回了，这里不能复用它们
    async with SessionLocal() as s:
        fresh = Customer(
            name=f"{PREFIX}甲部待预告-{STAMP}", owner_id=IDS["sales_a"],
            pool_status="private", created_by=IDS["sales_a"], level="Z",
            last_followup_at=datetime.now(UTC) - timedelta(days=30),
        )
        s.add(fresh)
        await s.flush()
        IDS["c7"] = fresh.id
        await s.commit()

    status, res = call("POST", "/public-pool/run-recycle", admin)
    check("回收扫描可执行", status, 200)
    notified = (res.get("data") or {}).get("notified_count")
    check_true("返回里带出实际发出的预告条数", isinstance(notified, int), f"notified_count={notified}")

    name_a = f"{PREFIX}甲部待预告-{STAMP}"

    def notices(token: str) -> list[tuple[str, str]]:
        """取该用户的全部通知 (标题, 正文)。

        客户名写在**正文**里（标题是固定文案），所以断言必须连正文一起看 ——
        只看标题会得出"没收到"的假结论。
        """
        _, res = call("GET", "/notifications?page_size=100", token)
        return [
            (row.get("title") or "", row.get("content") or "")
            for row in ((res.get("data") or {}).get("items") or [])
        ]

    # 原负责人：应收到一条提到自己客户名的预告
    mine = [
        (t, c) for t, c in notices(sales_a_t)
        if "回收预告" in t and name_a in c
    ]
    check("原负责人收到本客户的预告", len(mine), 1)
    check_true(
        "预告正文含回收原因与到期时间",
        bool(mine) and "预告期至" in mine[0][1] and "未跟进" in mine[0][1],
        mine[0][1][:90] if mine else "",
    )
    # 甲部主管：应收到待复核预告（这条候选在他管理范围内）
    mgr_a_rows = [(t, c) for t, c in notices(mgr_a) if "回收预告待复核" in t and name_a in c]
    check("本团队主管收到本客户的待复核预告", len(mgr_a_rows), 1)
    # 乙部主管：不该收到甲部客户的预告（按管理范围挑，不是有权限的都发）
    mgr_b_rows = [(t, c) for t, c in notices(mgr_b) if name_a in c]
    check("范围外的主管**不**收到该预告", len(mgr_b_rows), 0)

    # 去重：再扫一次，不该多出重复的预告
    call("POST", "/public-pool/run-recycle", admin)
    again = [(t, c) for t, c in notices(sales_a_t) if "回收预告" in t and name_a in c]
    check("重复扫描不重复发预告", len(again), 1)


# ---------------------------------------------------------------- 追加口径 1：配置

async def section3_config(admin: str) -> None:
    print("\n== 追加口径 1：参数可配、校验、审计、改配置一起重算 ==")
    # 造两条**还在等**的候选来做重算断言。
    # ⚠️ 不能用 `cand_deferred`：section1 已经把它**驳回结案**了，
    # 而按新口径结案的候选**不该**被重算 —— 它在这里的角色是**反例**。
    now = datetime.now(UTC)
    async with SessionLocal() as s:
        fresh_ids = []
        for tag in ("重算待复核", "重算已暂缓"):
            c = Customer(
                name=f"{PREFIX}{tag}-{STAMP}", owner_id=IDS["sales_a"],
                pool_status="private", created_by=IDS["sales_a"], level="Y",
                last_followup_at=now,
            )
            s.add(c)
            await s.flush()
            fresh_ids.append(c.id)
        await s.commit()

    def make_candidate(customer_id: int, status: str, **kw) -> PublicPoolRecycleCandidate:
        payload = {
            "customer_id": customer_id, "owner_id": IDS["sales_a"],
            "level": "Y", "rule_days": 1, "protection_snapshot": [],
            "status": status, "notice_at": now, "created_at": now, "notice_days": 7,
        }
        payload.update(kw)
        return PublicPoolRecycleCandidate(**payload)

    async with SessionLocal() as s:
        p = make_candidate(fresh_ids[0], "pending", due_at=now + timedelta(days=7))
        d = make_candidate(
            fresh_ids[1], "deferred",
            due_at=now + timedelta(days=7), defer_days=30,
            deferred_until=now + timedelta(days=30), decided_at=now,
        )
        s.add_all([p, d])
        await s.flush()
        open_pending, open_deferred = p.id, d.id
        await s.commit()

    # ⚠️ 这两个"改之前"的快照必须在**任何** PATCH 之前取 —— 下面"合法值"那一节
    # 就会改一次预告期，取晚了断言必假红。
    before_pending = await candidate_row(open_pending)
    before_closed = await candidate_row(IDS["cand_deferred"])

    status, _ = call(
        "PATCH", "/settings", admin,
        {"key": "pool_recycle_notice_days", "value": {"days": -3}},
    )
    check_in("预告期填负数 → 被拒", status, REJECTED)
    status, _ = call(
        "PATCH", "/settings", admin,
        {"key": "pool_recycle_defer_days", "value": {"days": 9999}},
    )
    check_in("暂缓期超出上限 → 被拒", status, REJECTED)
    status, _ = call(
        "PATCH", "/settings", admin,
        {"key": "pool_recycle_notice_days", "value": {"days": "七天"}},
    )
    check_in("预告期填非数字 → 被拒", status, REJECTED)
    # R14：小数与布尔曾经被 `int()` 悄悄收下（1.9→1、True→1）
    status, _ = call(
        "PATCH", "/settings", admin,
        {"key": "pool_recycle_notice_days", "value": {"days": 1.9}},
    )
    check_in("预告期填小数 1.9 → 被拒", status, REJECTED)
    status, _ = call(
        "PATCH", "/settings", admin,
        {"key": "pool_recycle_notice_days", "value": {"days": True}},
    )
    check_in("预告期填布尔 true → 被拒", status, REJECTED)
    status, _ = call(
        "PATCH", "/settings", admin,
        {"key": "pool_recycle_restore_permission", "value": {"text": "no:such:permission"}},
    )
    check_in("恢复权限填不存在的权限码 → 被拒", status, REJECTED)

    # 合法值：能存，且审计里有修改前后值
    status, _ = call(
        "PATCH", "/settings", admin,
        {"key": "pool_recycle_notice_days", "value": {"days": 5}},
    )
    check("合法预告期可以保存", status, 200)

    async with SessionLocal() as s:
        row = (
            await s.execute(
                text(
                    "select before_data, after_data from audit_logs "
                    "where business_type = 'setting' and action = 'update' "
                    "and business_id = (select id from system_settings "
                    "                   where key = 'pool_recycle_notice_days') "
                    "order by id desc limit 1"
                )
            )
        ).first()
    before_json = json.dumps(row[0], ensure_ascii=False) if row and row[0] else ""
    after_json = json.dumps(row[1], ensure_ascii=False) if row and row[1] else ""
    check_true("审计记下**修改前**的值", "value" in before_json, before_json[:90])
    check_true(
        "审计记下**修改后**的值",
        "days" in after_json and "5" in after_json,
        after_json[:90],
    )

    # 改配置**会连带重算**还没结案的候选 —— 口径已于 2026-10-06 变更：
    # 原来是"改配置不追溯旧候选"，主人明确要求改成"一起重算"，
    # 免得库里同时跑着两套天数。所以这里的断言跟着翻面。
    after_pending = await candidate_row(open_pending)
    check("改「预告期」→ 未结候选的天数快照跟着变", after_pending["notice_days"], 5)
    check_true(
        "改「预告期」→ 预告到期时间被重算",
        after_pending["due_at"] != before_pending["due_at"],
        f"改前 {before_pending['due_at']} → 改后 {after_pending['due_at']}",
    )
    after_closed = await candidate_row(IDS["cand_deferred"])
    check(
        "**已驳回**的候选不动（结案了，重算等于改历史）",
        after_closed["notice_days"], before_closed["notice_days"],
    )

    # 改「暂缓期」→ 已经暂缓着的那条重算暂缓到期时间
    before_defer = await candidate_row(open_deferred)
    call("PATCH", "/settings", admin, {"key": "pool_recycle_defer_days", "value": {"days": 10}})
    after_defer = await candidate_row(open_deferred)
    check("改「暂缓期」→ 候选的暂缓天数跟着变", after_defer["defer_days"], 10)
    check_true(
        "改「暂缓期」→ 暂缓到期时间被重算",
        after_defer["deferred_until"] != before_defer["deferred_until"],
        f"改前 {before_defer['deferred_until']} → 改后 {after_defer['deferred_until']}",
    )

    # 恢复默认，避免影响后面的用例
    call("PATCH", "/settings", admin, {"key": "pool_recycle_notice_days", "value": {"days": 7}})
    call("PATCH", "/settings", admin, {"key": "pool_recycle_defer_days", "value": {"days": 30}})


# ---------------------------------------------------------------- 第 9 条：打样修订链

async def section4_sample_chain() -> None:
    print("\n== 第 9 条：被替代的旧版打样不再独立保护 ==")
    async with SessionLocal() as s:
        detail = await settings_service.protection_detail(s)

    # ⚠️ 必须把 id 抠出来做**精确**比对：直接 `"#1" in 文本` 会被 "#11" 命中，
    # 得到的是假结论（换成"不该出现"那一侧就是假绿）。
    protected_ids = {
        int(m)
        for m in re.findall(r"打样单 #(\d+)", "；".join(detail.get(IDS["c3"], [])))
    }
    shown = sorted(protected_ids)
    check_true(
        "V1 已被 V2 替代（V2 客户已接受）→ 这张链不再保护",
        IDS["chain_a_v1"] not in protected_ids,
        f"保护中的打样={shown}，被替代的 V1={IDS['chain_a_v1']}",
    )
    check_true(
        "V1→V2→V3 且 V3 未完成 → 最新版继续保护",
        IDS["chain_b_v3"] in protected_ids,
        f"保护中的打样={shown}，V3={IDS['chain_b_v3']}",
    )
    check_true(
        "同客户的**独立**打样各自保护",
        IDS["solo"] in protected_ids,
        f"保护中的打样={shown}，独立打样={IDS['solo']}",
    )

    # 历史没有被删：旧版行还在（只是不再承担保护）
    async with SessionLocal() as s:
        alive = (
            await s.execute(
                text("select count(*) from sample_requests where id = :i"),
                {"i": IDS["chain_a_v1"]},
            )
        ).scalar_one()
    check("旧版资料仍然留着（不删历史）", int(alive), 1)


# ---------------------------------------------------------------- 第 10 条：批量与页签

async def section5_batch(admin: str, mgr_a: str) -> None:
    print("\n== 第 10 条：批量接口统一请求体 + 各状态都能列 ==")
    # 统一成"一次请求体带齐 ids + decision"
    status, res = call(
        "POST", "/public-pool/recycle-candidates/batch-decide", admin,
        {"candidate_ids": [], "decision": "reject"},
    )
    check_in("空 ids → 明确的参数错误（不是 500）", status, REJECTED)

    # 各状态页签都能列出来（此前页面只查 pending）
    for state in ("pending", "deferred", "executed", "rejected"):
        status, res = call(
            "GET", f"/public-pool/recycle-candidates?status={state}&page=1&page_size=5", admin
        )
        check(f"status={state} 可以查询", status, 200)

    # 单条 decide 的响应里带"最早可回收时间"，前端据此才敢灰掉按钮
    async with SessionLocal() as s:
        now = datetime.now(UTC)
        row = PublicPoolRecycleCandidate(
            customer_id=IDS["c3"], owner_id=IDS["sales_a"], level="Z", rule_days=1,
            last_active_at=now - timedelta(days=30), protection_snapshot=[],
            status="pending", notice_at=now, created_at=now, notice_days=7,
            due_at=now + timedelta(days=2),
        )
        s.add(row)
        await s.flush()
        cid = row.id
        await s.commit()
    _, res = call("GET", f"/public-pool/recycle-candidates?status=pending&page_size=50", admin)
    items = (res.get("data") or {}).get("items") or []
    mine = next((x for x in items if x.get("id") == cid), None)
    check_true("列表行带 earliest_action_at", bool(mine and mine.get("earliest_action_at")),
               str(mine and mine.get("earliest_action_at")))
    # 批量的逐条结果：这条没到期 → 应出现在 failed 里，而不是把整批拖停
    status, res = call(
        "POST", "/public-pool/recycle-candidates/batch-decide", admin,
        {"candidate_ids": [cid], "decision": "approve"},
    )
    check("批量接口能调通（请求体统一后）", status, 200)
    data = res.get("data") or {}
    check("未到期的那条逐条报失败", len(data.get("failed") or []), 1)


# ---------------------------------------------------------------- 追加口径 2：恢复

async def section6_restore(mgr_a: str, sales_role_token: str | None) -> None:
    print("\n== 追加口径 2：恢复权限与冲突 ==")
    async with SessionLocal() as s:
        now = datetime.now(UTC)
        # 一个"已回收"的客户（owner 为空、在公海），候选记着原负责人
        cust = Customer(
            name=f"{PREFIX}已回收客户-{STAMP}", owner_id=None,
            pool_status="public", created_by=IDS["mgr_a"], level="Y",
        )
        s.add(cust)
        await s.flush()
        row = PublicPoolRecycleCandidate(
            customer_id=cust.id, owner_id=IDS["sales_a"], level="Z", rule_days=1,
            last_active_at=now - timedelta(days=30), protection_snapshot=[],
            status="executed", notice_at=now, created_at=now,
            decided_at=now, executed_at=now,
        )
        s.add(row)
        await s.flush()
        IDS["cand_executed"] = row.id
        IDS["c5"] = cust.id
        await s.commit()

    # 普通业务员（无 customer:assign）→ 不能恢复
    if sales_role_token:
        status, _ = call(
            "POST", f"/public-pool/recycle-candidates/{IDS['cand_executed']}/restore",
            sales_role_token, {"note": "试一下"},
        )
        check_in("普通业务员不能恢复", status, DENIED)

    # 本团队主管 → 能恢复
    status, _ = call(
        "POST", f"/public-pool/recycle-candidates/{IDS['cand_executed']}/restore",
        mgr_a, {"note": f"{PREFIX}客户确实还在跟"},
    )
    check("主管能恢复本团队客户", status, 200)
    check("客户已还给原负责人", await owner_of(IDS["c5"]), IDS["sales_a"])

    # 已被别人领走 → 报冲突，不覆盖
    async with SessionLocal() as s:
        now = datetime.now(UTC)
        cust = Customer(
            name=f"{PREFIX}被领走客户-{STAMP}", owner_id=IDS["mgr_b"],
            pool_status="private", created_by=IDS["mgr_b"], level="Y",
        )
        s.add(cust)
        await s.flush()
        row = PublicPoolRecycleCandidate(
            customer_id=cust.id, owner_id=IDS["sales_a"], level="Z", rule_days=1,
            last_active_at=now - timedelta(days=30), protection_snapshot=[],
            status="executed", notice_at=now, created_at=now,
            decided_at=now, executed_at=now,
        )
        s.add(row)
        await s.flush()
        IDS["cand_taken"] = row.id
        IDS["c6"] = cust.id
        await s.commit()

    status, _ = call(
        "POST", f"/public-pool/recycle-candidates/{IDS['cand_taken']}/restore",
        mgr_a, {"note": f"{PREFIX}想还给原负责人"},
    )
    check("客户已被别人领取 → 报冲突（409）", status, 409)
    check("冲突时**不覆盖**现有归属", await owner_of(IDS["c6"]), IDS["mgr_b"])


async def main() -> None:
    url = os.environ.get("DATABASE_URL", "")
    assert "test" in url or os.environ.get("CI") == "true", (
        "必须在隔离库跑：DATABASE_URL 里要含 test"
    )
    print(f"目标：{BASE}")
    await cleanup()
    await build_fixtures()
    await build_sample_chain()

    admin = login("admin", "admin123")
    mgr_a_t = login(f"{PREFIX.lower()}_mgra_{STAMP}", "123456")
    mgr_b_t = login(f"{PREFIX.lower()}_mgrb_{STAMP}", "123456")
    sales_a_t = login(f"{PREFIX.lower()}_salesa_{STAMP}", "123456")
    sales_b_t = login(f"{PREFIX.lower()}_salesb_{STAMP}", "123456")

    try:
        await section1_deadlines(mgr_a_t)
        await section2_notice(admin, mgr_a_t, mgr_b_t, sales_a_t)
        await section3_config(admin)
        await section4_sample_chain()
        await section5_batch(admin, mgr_a_t)
        # 传甲部业务员：它在候选的数据范围内，**但没有** customer:assign ——
        # 这样才能测出"权限"这道关，而不是被数据范围先拦掉
        await section6_restore(mgr_a_t, sales_a_t)
    finally:
        await cleanup()

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项：")
        for item in FAILURES:
            print("  -", item)
        raise SystemExit(1)
    print("全部通过")


if __name__ == "__main__":
    asyncio.run(main())
