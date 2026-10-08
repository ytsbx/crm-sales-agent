#!/usr/bin/env python
"""第十批组四：线索分配入口的权限（10.3）、部门「本部门及下级」的递归范围（10.9）。

**只在隔离库跑**：库名必须含 test，且推送开关全关、**必须显式给 API_BASE**。

## 守的两件事

**10.3「建线索时指定负责人」是分配动作，不是创建动作。**
`POST /leads` 从前只要 `lead:create`；带上 `owner_id` 就等于完成了变相分配 ——
业务员（有 create、无 assign）在页面上**看不见**"分配线索"按钮，接口这条路却通着。
另外那条旧路只拿**数据范围**卡了一下目标、且**没判"这个人存不存在、在不在岗"**
（另外四个分配入口都拦得住，只有这扇门漏着）。
口径（2026-10-08 定）：有"分配线索"权限的人可以分给**任何人（含跨部门）**，
分配**不看数据范围**。

**10.9「本部门及下级」要往下递归到底。**
`data_scope.py` 里两处递归 CTE 都写成了 `select(subtree.c.id).union_all(...)`：
CTE 的定义里就只剩"起点部门"这一条，递归那半句落到了**外层** `UNION ALL` 上，
而外层只执行一次 —— 整棵树**只往下展开一层**，孙部门一律漏掉。
影响两块：数据范围（"含下级"的主管看不到孙部门同事的数据）、团队目标的成员集合
（部门实绩少算孙部门的人）。

⚠️ 这条**必须用三层以上的部门树测**：两层时"本部门及下级"恰好就是那两个部门，
看起来完全正确。本套件因此造的是**三层**（甲部 → 甲部子部 → 甲部孙部），
第四批的 `check_fourth_round_fixes` 只造了两层，所以一直没抓到。

跑法（隔离库）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_iso_test_xxx \\
      PYTHONPATH=. .venv/bin/python scripts/check_tenth_round_scope_assign.py
"""

import asyncio
import json
import os
import time
import urllib.error
import urllib.request

import app.main  # noqa: F401  保证所有模型都注册进 metadata

_ = app.main  # 显式"用"一下：只 import 不带这一句，pyflakes 会当成未使用

from sqlalchemy import select, text

from app.core.data_scope import (
    department_member_ids,
    department_subtree_ids_stmt,
)
from app.core.database import SessionLocal

if not os.environ.get("API_BASE"):
    raise SystemExit(
        "必须显式设置 API_BASE（不能依赖默认的 8000，那是开发后端）：\n"
        "  API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_iso_test_xxx \\\n"
        "    PYTHONPATH=. .venv/bin/python scripts/check_tenth_round_scope_assign.py"
    )
BASE = os.environ["API_BASE"].rstrip("/")

PREFIX = f"CHKSCOPE{int(time.time())}"
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


class _ScopeUser:
    """只需要 `department_subtree_ids_stmt` 读的那一个字段，别为它建一整条账号。"""

    def __init__(self, department_id: int) -> None:
        self.id = 0
        self.department_id = department_id
        self.data_scope = "department_and_sub"


async def seed() -> dict:
    """造三层部门树 + 三种角色 + 四个账号。

    部门树是**三层**：甲部 → 甲部子部 → 甲部孙部。这是本套件的重点 ——
    只造两层就测不出 10.9（"本部门及下级"在两层树上恰好等于全部）。
    """
    from app.core.security import hash_password
    from app.modules.user.model import (
        Department,
        Permission,
        Role,
        User,
        role_permissions,
        user_roles,
    )

    stamp = int(time.time())
    pwd = hash_password("123456")
    ids: dict = {}

    async with SessionLocal() as s:
        top = Department(name=f"{PREFIX}甲部-{stamp}")
        mid_d = Department(name=f"{PREFIX}甲部子部-{stamp}")
        low_d = Department(name=f"{PREFIX}甲部孙部-{stamp}")
        s.add_all([top, mid_d, low_d])
        await s.flush()
        mid_d.parent_id = top.id
        low_d.parent_id = mid_d.id
        await s.flush()

        def new_role(code: str, name: str, scope: str) -> Role:
            return Role(code=code, name=name, status="active", data_scope=scope)

        role_assign = new_role(f"{PREFIX}ASSIGN", f"{PREFIX}主管(可分配)", "department_and_sub")
        role_create = new_role(f"{PREFIX}CREATE", f"{PREFIX}业务(仅建)", "self")
        role_dept = new_role(f"{PREFIX}DEPT", f"{PREFIX}主管(只本部门)", "department")
        s.add_all([role_assign, role_create, role_dept])
        await s.flush()

        codes = ["lead:create", "lead:assign", "lead:view"]
        perms = {}
        for code in codes:
            row = (
                await s.execute(select(Permission).where(Permission.code == code))
            ).scalars().first()
            if row is None:
                raise SystemExit(f"权限码 {code} 不存在，先跑 scripts/seed.py")
            perms[code] = row.id

        grants = {
            role_assign: codes,
            role_create: ["lead:create", "lead:view"],
            role_dept: codes,
        }
        for role, grant in grants.items():
            for code in grant:
                await s.execute(
                    role_permissions.insert().values(role_id=role.id, permission_id=perms[code])
                )
        await s.flush()

        accounts = [
            ("boss", top.id, role_assign, "active"),
            ("depthead", top.id, role_dept, "active"),
            ("mid", mid_d.id, role_create, "active"),
            ("low", low_d.id, role_create, "active"),
            ("off", low_d.id, role_create, "inactive"),  # 已停用，用来验"不能分给停用账号"
        ]
        for key, dept_id, role, status in accounts:
            user = User(
                username=f"{PREFIX.lower()}_{key}_{stamp}",
                name=f"{PREFIX}{key}",
                password_hash=pwd,
                status=status,
                department_id=dept_id,
            )
            s.add(user)
            await s.flush()
            await s.execute(user_roles.insert().values(user_id=user.id, role_id=role.id))
            ids[key] = {"id": user.id, "username": user.username}

        ids["dept"] = {"top": top.id, "mid": mid_d.id, "low": low_d.id}
        ids["role_ids"] = [role_assign.id, role_create.id, role_dept.id]
        await s.commit()
    return ids


async def cleanup() -> None:
    """清干净本套件建的线索 / 分配历史 / 审计 / 账号 / 角色 / 部门。

    ⚠️ 按**范围**删（名字前缀），不按"记过账的 id"删：断言失败时那些
    "本该被拒却真落了库"的线索不会进账，只按 id 删就会漏（组二踩过这个坑）。
    """
    async with SessionLocal() as s:
        lead_ids = "(select id from leads where name like :p)"
        for sql, params in (
            ("delete from lead_assignments where lead_id in " + lead_ids, {"p": f"{PREFIX}%"}),
            ("delete from leads where name like :p", {"p": f"{PREFIX}%"}),
            (
                "delete from audit_logs where coalesce(after_data::text, '') like :m"
                " or coalesce(before_data::text, '') like :m",
                {"m": f"%{PREFIX}%"},
            ),
            (
                "delete from user_roles where user_id in "
                "(select id from users where username like :u)",
                {"u": f"{PREFIX.lower()}%"},
            ),
            (
                "delete from user_roles where role_id in (select id from roles where code like :r)",
                {"r": f"{PREFIX}%"},
            ),
            (
                "delete from role_permissions where role_id in "
                "(select id from roles where code like :r)",
                {"r": f"{PREFIX}%"},
            ),
            ("delete from roles where code like :r", {"r": f"{PREFIX}%"}),
            # 用户引用部门，必须先删用户再删部门
            ("delete from users where username like :u", {"u": f"{PREFIX.lower()}%"}),
            ("delete from departments where name like :p", {"p": f"{PREFIX}%"}),
        ):
            await s.execute(text(sql), params)
        await s.commit()


async def count_leads_by_name() -> int:
    async with SessionLocal() as s:
        return int(
            (
                await s.execute(
                    text("select count(*) from leads where name like :p"), {"p": f"{PREFIX}%"}
                )
            ).scalar_one()
        )


async def lead_row(lead_id: int) -> dict:
    async with SessionLocal() as s:
        row = (
            await s.execute(
                text("select owner_id, status from leads where id = :i"), {"i": lead_id}
            )
        ).first()
    return {"owner_id": row[0] if row else None, "status": row[1] if row else None}


async def assignment_rows(lead_id: int) -> list[dict]:
    async with SessionLocal() as s:
        rows = (
            await s.execute(
                text(
                    "select from_user_id, to_user_id, reason from lead_assignments"
                    " where lead_id = :i order by id"
                ),
                {"i": lead_id},
            )
        ).all()
    return [{"from": r[0], "to": r[1], "reason": r[2]} for r in rows]


async def main() -> int:
    login("admin", "admin123")  # 顺带确认后端在跑：登录失败会直接 SystemExit
    ids = await seed()
    print(f"夹具：三层部门 甲部={ids['dept']['top']} → 子部={ids['dept']['mid']}"
          f" → 孙部={ids['dept']['low']}")

    boss = login(ids["boss"]["username"], "123456")
    depthead = login(ids["depthead"]["username"], "123456")
    mid = login(ids["mid"]["username"], "123456")

    try:
        # ============================================ 10.9 部门递归范围
        print()
        print("=== 1. 10.9「本部门及下级」要递归到底（三层树，二层测不出来）===")
        user = _ScopeUser(ids["dept"]["top"])
        async with SessionLocal() as s:
            rows = sorted(
                int(x) for x in (await s.execute(department_subtree_ids_stmt(user))).scalars().all()
            )
            members = await department_member_ids(s, ids["dept"]["top"])
        expect = sorted([ids["dept"]["top"], ids["dept"]["mid"], ids["dept"]["low"]])
        check("「甲部及其下级」包含甲部+子部+孙部（修复前只到子部）", rows, expect)

        check_true(
            "部门成员集合把孙部门的人也算进来（团队目标实绩靠它加总）",
            ids["low"]["id"] in members,
            f"low={ids['low']['id']}，集合={members}",
        )

        # ---- 接口级：让数据真的挂在孙部门的人名下，再看主管能不能查到 ----
        # boss 在甲部、范围"本部门及下级"；他用"创建时指定负责人"把线索挂在**孙部门**的
        # low 名下（跨了两层）。然后他自己、以及"只看本部门"的 depthead 各查一次。
        status, res = call(
            "POST",
            "/leads",
            token=boss,
            body={"name": f"{PREFIX}孙部线索", "owner_id": ids["low"]["id"]},
        )
        check("主管（含下级）把线索分给孙部门同事 → 200", status, 200)
        deep_lead = res.get("data", {}).get("id")

        def visible_ids(token: str) -> set[int]:
            _, page = call("GET", f"/leads?keyword={PREFIX}&page_size=100", token=token)
            return {int(it["id"]) for it in (page.get("data") or {}).get("items", [])}

        check_true(
            "含下级范围的主管**看得到**孙部门同事名下的线索",
            deep_lead in visible_ids(boss),
            f"线索 {deep_lead}",
        )
        check_true(
            "对照：只本部门范围的主管**看不到**孙部门的线索（证明边界是对的，没有一刀切放开）",
            deep_lead not in visible_ids(depthead),
        )

        # ============================================ 10.3 建线索时的分配权限
        print()
        print("=== 2. 10.3 建线索时「指定负责人」= 分配动作，要有分配权限 ===")
        before = await count_leads_by_name()

        status, res = call("POST", "/leads", token=mid, body={"name": f"{PREFIX}仅建-无负责人"})
        check("只有「建线索」权限的账号，不带负责人建线索 → 200（照常进池）", status, 200)
        pool_lead = res.get("data", {}).get("id")

        status, res = call(
            "POST", "/leads", token=mid,
            body={"name": f"{PREFIX}仅建-挂自己", "owner_id": ids["mid"]["id"]},
        )
        check("只有「建线索」权限的账号，带负责人（哪怕是自己）→ 403", status, 403)

        status, res = call(
            "POST", "/leads", token=mid,
            body={"name": f"{PREFIX}仅建-挂别人", "owner_id": ids["low"]["id"]},
        )
        check("只有「建线索」权限的账号，把线索挂到别人名下 → 403", status, 403)

        status, _ = call(
            "POST", "/leads", token=boss,
            body={"name": f"{PREFIX}主管-不存在的人", "owner_id": 999999999},
        )
        check("有分配权限，但负责人 id 不存在 → 404（旧路只判范围，会原样写库）", status, 404)

        status, _ = call(
            "POST", "/leads", token=boss,
            body={"name": f"{PREFIX}主管-已停用", "owner_id": ids["off"]["id"]},
        )
        check("有分配权限，但负责人已停用 → 422", status, 422)

        after = await count_leads_by_name()
        check(
            "被拒的三条一条都没落库（只该多出「无负责人」那一条）",
            after - before,
            1,
        )

        # ============================================ 落库与留痕
        print()
        print("=== 3. 分配动作的落地与留痕 ===")
        check("进池那条的负责人为空", (await lead_row(pool_lead))["owner_id"], None)
        check("进池那条的状态是待分配", (await lead_row(pool_lead))["status"], "pending")

        row = await lead_row(deep_lead)
        check("创建时分配的线索，负责人落在目标身上", row["owner_id"], ids["low"]["id"])
        check("状态变成已分配", row["status"], "assigned")

        history = await assignment_rows(deep_lead)
        check("分配历史记了一条", len(history), 1)
        if history:
            check("历史里的接收人是目标本人", history[0]["to"], ids["low"]["id"])
            check_true(
                "分配历史里写了「创建线索时指定负责人」这个来由",
                "创建线索" in (history[0]["reason"] or ""),
                history[0]["reason"],
            )

        # ============================================ 收尾自检
        print()
        print("=== 4. 收尾自检 ===")
        check("套件建的线索都在（没有被误删）", await count_leads_by_name(), 2)
    finally:
        await cleanup()
        print()
        print(f"  已清 {PREFIX} 夹具")

    if FAILURES:
        print(f"\n失败 {len(FAILURES)} 项：{FAILURES}")
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
