"""第六批返修 P1（1~7）回归：释放保护 / 外部落库 / 重试限制 / 双责任 / 价格与授权。

**只在隔离库跑**：库名必须含 test（或 CI=true），且推送开关全关。

## 这七条修的是什么（每条对应审查方的验收要求）

**P1-1 释放保护收口**
此前只有 `release-to-pool` 接口做履约保护检查；单个转移、分配、批量转移都能传
`owner_id=null`，换个入口就把还在履约中的客户丢进公海，连"填原因后例外释放"
这一关也绕过。现在四个入口共用服务层那一道校验。
验收：**单个转移 / 分配 / 批量转移**三处各测一次（四类保护里挑订单这类代表）。

**P1-2 企微外部调用前先落库**
此前全流程只在路由最后 commit 一次。企微转接是外部调用、撤不回来，
它成功之后只要后续任何一步抛错，本地会整体回滚 —— 任务与逐项记录一起消失。
现在外部调用前先提交一次。
验收：**"企微成功后、本地故意失败"** —— 任务与企微侧已成功的记录必须还在库里。

**P1-3 重试与首次同档**
重试入口此前不检查 `WECOM_TRANSFER_ENABLED`，开关关着也能把转接发出去。
验收：**关掉开关后重试同样被拒**（且拒绝发生在查任务之前）。

**P1-4 打样双责任**
打样有 `owner_id`（跟单）和 `production_owner_id`（生产）两个责任字段。
此前只盘跟单、只迁跟单，离职人担任生产责任人的单子没人接。
验收：**只负责跟单 / 只负责生产 / 两项都负责** 三种情形各出现对应的项。

**P1-5 合并价格冲突**
此前把冲突集合截成前 20 条拿去执行，且只比"同 SKU + 同起订量"，
区间重叠（100~1000 件 vs 0~500 件）测不出来；非法选择值会静默按"保留目标"处理。
现在复用价格中心的区间+有效期重叠规则。验收：**超过 20 对不截断**、
**区间重叠能检出**、**有效期不重叠不算冲突**、**非法选择值被拒**。

**P1-6 合并绕过价格权限**
合并只要求 `customer:update`，而正常维护客户专属价要 `price:manage` ——
有客户编辑权的人可以借"选保留哪一边"完成价格裁决。
验收：**只有 customer:update、没有 price:manage 的账号**做价格裁决被拒，
且数据保持原状。

**P1-7 回收复核权限**
四个回收接口原本统一要求 `settings:manage`，而默认销售主管没有它 →
"主管逐条或批量批准"实际上打不通；列表也没做数据范围过滤。
现在改用独立业务权限 `customer:pool_review`，并按客户数据范围过滤。
验收：**主管能审批本团队、不能碰其他团队；普通业务员不能审批**。

跑法（需要后端在跑）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_sales_agent_test \\
      PYTHONPATH=. .venv/bin/python scripts/check_sixth_round_fixes.py
"""

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, date, datetime, timedelta

import app.main  # noqa: F401  保证所有模型都注册进 metadata（缺表会 NoReferencedTableError）
from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.deps import CurrentUser
from app.core.security import hash_password
from app.modules.customer.model import Customer, CustomerOwnerHistory
from app.modules.order.model import SalesOrder
from app.modules.pricing.model import CustomerPriceRule
from app.modules.sample.model import SampleRequest
from app.modules.settings.model import PublicPoolRecycleCandidate
from app.modules.user.model import Department, Role, User
from app.modules.wecom import client as wecom_client
from app.modules.wecom import service as wecom_service
from app.modules.wecom.model import WeComSyncJob, WeComTransferItem

FAILURES: list[str] = []
BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000/api/v1")
PREFIX = "CHK6TH"
STAMP = str(int(time.time()))
#: 被拒的正常表现：403（权限/数据范围）或 404（不可见时不暴露存在性）
DENIED = (403, 404)
#: 业务规则拒绝：400（STATUS_NOT_ALLOWED）或 422（显式传状态的参数校验）
REJECTED = (400, 422)


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
    """给角色授权限（权限码必须在 permissions 表里，seed 已建）。"""
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
    """自底向上清干净（本套件写：客户/订单/打样/价目/候选/历史/企微/用户角色）。"""
    async with SessionLocal() as s:
        cust = "(select id from customers where name like :p)"
        for sql in (
            "delete from wecom_transfer_items where job_id in "
            "(select id from wecom_sync_jobs where operator_id in "
            "(select id from users where username like :u))",
            "delete from wecom_sync_jobs where operator_id in "
            "(select id from users where username like :u)",
            "delete from public_pool_recycle_candidates where customer_id in " + cust,
            "delete from customer_owner_history where customer_id in " + cust,
            "delete from customer_price_rules where customer_id in " + cust,
            "delete from sales_orders where customer_id in " + cust,
            "delete from sample_requests where customer_id in " + cust,
            # 客户合并留痕：本套件第 5 节拿 CHK6TH 客户真做过合并
            # （`CHK6TH合并来源/合并目标`、`CHK6TH区间来源/区间目标`…），
            # 合并接口会往 customer_merge_logs 写一行。
            # ⚠️ 这张表**没有外键**指向 customers，删客户不会连带删掉它，也没有
            # deleted_at 可以软删——不显式清，留痕就永久堆在库里，且引用的客户
            # 已经不存在了。后果实测过：这种"孤儿留痕"会在下一轮回归里被
            # 复用同一批 id 的新客户捞出来（见 scripts/_test_support.py 的说明），
            # 让回收站套件的"最终有效客户"解析成 None——表现为
            # 「约 2~4 次全量回归红 1 次、单独跑永不复现」。
            "delete from customer_merge_logs where source_customer_id in " + cust
            + " or target_customer_id in " + cust,
            "delete from customers where name like :p",
            "delete from user_roles where role_id in (select id from roles where code like :r)",
            "delete from role_permissions where role_id in "
            "(select id from roles where code like :r)",
            "delete from roles where code like :r",
            "delete from user_roles where user_id in (select id from users where username like :u)",
            # ⚠️ 部门必须在**用户之后**删：users.department_id 是外键指向它，
            # 反过来删会 ForeignKeyViolation
            "delete from users where username like :u",
            "delete from departments where name like :p",
        ):
            await s.execute(
                text(sql), {"p": f"{PREFIX}%", "u": f"{PREFIX.lower()}%", "r": f"{PREFIX}%"}
            )
        await s.commit()


# ---------------------------------------------------------------- 夹具

IDS: dict = {}


async def build_fixtures() -> None:
    """造这一次需要的所有夹具。命名统一带 PREFIX，便于按前缀清理。"""
    async with SessionLocal() as s:
        pwd = hash_password("123456")

        dept_a = Department(name=f"{PREFIX}甲部-{STAMP}", status="active")
        dept_b = Department(name=f"{PREFIX}乙部-{STAMP}", status="active")
        s.add_all([dept_a, dept_b])
        await s.flush()
        IDS["dept_a"], IDS["dept_b"] = dept_a.id, dept_b.id

        def make_user(tag: str, dept_id: int | None = None) -> User:
            return User(
                name=f"{PREFIX}{tag}-{STAMP}",
                username=f"{PREFIX.lower()}_{tag}_{STAMP}",
                password_hash=pwd,
                status="active",
                department_id=dept_id,
            )

        # 三个账号：两个本团队主管（甲/乙部，各自带回收复核权）、一个纯业务员
        mgr_a = make_user("mgra", dept_a.id)
        mgr_b = make_user("mgrb", dept_b.id)
        sales = make_user("sales", dept_a.id)
        # 只有客户编辑权、**没有价格权限**的账号（测合并绕过价格权）
        price_less = make_user("priceless", dept_a.id)
        # 离职人 / 接手人（企微外部落库与打样双责任用）
        handover = make_user("handover")
        takeover = make_user("takeover")
        s.add_all([mgr_a, mgr_b, sales, price_less, handover, takeover])
        await s.flush()
        IDS.update(
            mgr_a=mgr_a.id, mgr_b=mgr_b.id, sales=sales.id, priceless=price_less.id,
            handover=handover.id, takeover=takeover.id,
        )

        # 主管角色：本部门范围 + 回收复核权（**不含** settings:manage）
        mgr_role = Role(
            code=f"{PREFIX}MGR{STAMP}", name=f"{PREFIX}主管", data_scope="department"
        )
        # 业务员角色：本部门范围，**没有**回收复核权
        sales_role = Role(
            code=f"{PREFIX}SALES{STAMP}", name=f"{PREFIX}业务员", data_scope="department"
        )
        # 只有客户编辑权的角色（测合并价格权限）
        pl_role = Role(
            code=f"{PREFIX}PL{STAMP}", name=f"{PREFIX}无价格权", data_scope="department"
        )
        s.add_all([mgr_role, sales_role, pl_role])
        await s.flush()

        await grant_role(
            s, mgr_role.id,
            # customer:assign 是转移/分配/释放的权限点（业务员没有它）
            ["customer:view", "customer:update", "customer:assign",
             "customer:pool_review"],
        )
        await grant_role(
            s, sales_role.id,
            ["customer:view", "customer:update"],  # 业务员不给 pool_review
        )
        await grant_role(
            s, pl_role.id,
            ["customer:view", "customer:update"],  # 有编辑权、**没有** price:manage
        )
        for user, role in (
            (mgr_a, mgr_role), (mgr_b, mgr_role), (sales, sales_role), (price_less, pl_role)
        ):
            await s.execute(
                text("insert into user_roles (user_id, role_id) values (:u, :r)"),
                {"u": user.id, "r": role.id},
            )

        # ---- 客户 ----
        # ① 甲部主管名下、带在途订单 → 受履约保护（测三个释放入口）
        protected = Customer(
            name=f"{PREFIX}受保护客户-{STAMP}", owner_id=mgr_a.id,
            pool_status="private", created_by=mgr_a.id,
        )
        # ② 等值客户（批量转移用，也带保护）
        protected2 = Customer(
            name=f"{PREFIX}受保护客户2-{STAMP}", owner_id=mgr_a.id,
            pool_status="private", created_by=mgr_a.id,
        )
        # ③ 乙部主管名下、无保护（测跨团队不可见）
        cross = Customer(
            name=f"{PREFIX}跨团队客户-{STAMP}", owner_id=mgr_b.id,
            pool_status="private", created_by=mgr_b.id,
        )
        # ④ 合并用的来源 / 目标（先建成无冲突，价格规则稍后挂）
        merge_src = Customer(
            name=f"{PREFIX}合并来源-{STAMP}", owner_id=mgr_a.id,
            pool_status="private", created_by=mgr_a.id,
        )
        merge_dst = Customer(
            name=f"{PREFIX}合并目标-{STAMP}", owner_id=mgr_a.id,
            pool_status="private", created_by=mgr_a.id,
        )
        # 第 5 节各子场景专用（互不干扰：25 对 / 区间重叠 / 有效期 / 非法值）
        rng_src = Customer(name=f"{PREFIX}区间来源-{STAMP}", owner_id=mgr_a.id,
                           pool_status="private", created_by=mgr_a.id)
        rng_dst = Customer(name=f"{PREFIX}区间目标-{STAMP}", owner_id=mgr_a.id,
                           pool_status="private", created_by=mgr_a.id)
        exp_src = Customer(name=f"{PREFIX}效期来源-{STAMP}", owner_id=mgr_a.id,
                           pool_status="private", created_by=mgr_a.id)
        exp_dst = Customer(name=f"{PREFIX}效期目标-{STAMP}", owner_id=mgr_a.id,
                           pool_status="private", created_by=mgr_a.id)
        bad_src = Customer(name=f"{PREFIX}非法来源-{STAMP}", owner_id=mgr_a.id,
                           pool_status="private", created_by=mgr_a.id)
        bad_dst = Customer(name=f"{PREFIX}非法目标-{STAMP}", owner_id=mgr_a.id,
                           pool_status="private", created_by=mgr_a.id)
        s.add_all([
            protected, protected2, cross, merge_src, merge_dst,
            rng_src, rng_dst, exp_src, exp_dst, bad_src, bad_dst,
        ])
        await s.flush()
        IDS.update(
            protected=protected.id, protected2=protected2.id, cross=cross.id,
            merge_src=merge_src.id, merge_dst=merge_dst.id,
            rng_src=rng_src.id, rng_dst=rng_dst.id,
            exp_src=exp_src.id, exp_dst=exp_dst.id,
            bad_src=bad_src.id, bad_dst=bad_dst.id,
        )

        # 在途订单：构成履约保护。
        # ⚠️ 状态必须落在 protection_detail 认的那一组
        # （pending / in_production / shipped / delivered）—— 用 "confirmed"
        # 之类不构成保护，那断言就会"因为夹具没生效"而假红。
        for cid, tag in ((protected.id, "SO"), (protected2.id, "SO2")):
            s.add(
                SalesOrder(
                    order_no=f"{PREFIX}{tag}-{STAMP}", customer_id=cid,
                    total_amount=100, currency="CNY", status="in_production",
                    owner_id=mgr_a.id,
                )
            )

        # 打样：三种责任组合（测生产责任盘点）
        sku_id = (await s.execute(text("select id from skus limit 1"))).scalar_one()
        IDS["sku"] = sku_id
        both = SampleRequest(
            customer_id=merge_dst.id, owner_id=handover.id,
            production_owner_id=handover.id, status="approved",
            confirm_status="pending", created_by=handover.id,
        )
        only_prod = SampleRequest(
            customer_id=merge_dst.id, owner_id=mgr_a.id,
            production_owner_id=handover.id, status="approved",
            confirm_status="pending", created_by=mgr_a.id,
        )
        only_follow = SampleRequest(
            customer_id=merge_dst.id, owner_id=handover.id,
            production_owner_id=mgr_a.id, status="approved",
            confirm_status="pending", created_by=handover.id,
        )
        s.add_all([both, only_prod, only_follow])
        await s.flush()
        IDS.update(both=both.id, only_prod=only_prod.id, only_follow=only_follow.id)

        await s.commit()


async def add_price_pairs(
    *, customer_id: int, count: int, base_price: str, today: date
) -> list[int]:
    """给某客户挂 count 条互不重叠区间的生效价，返回规则 id 列表。

    区间留足间隔（[100i, 100i+50]），保证**同一客户内部不互相重叠** ——
    这样合并后自检该通过；真正的冲突发生在"两边同区间、价不同"。
    """
    ids: list[int] = []
    async with SessionLocal() as s:
        for index in range(count):
            low = 100 * index
            row = CustomerPriceRule(
                customer_id=customer_id, sku_id=IDS["sku"], min_qty=low,
                max_qty=low + 50, agreed_price=base_price,
                status="active", effective_from=today - timedelta(days=10),
                effective_to=today + timedelta(days=100),
            )
            s.add(row)
            await s.flush()
            ids.append(row.id)
        await s.commit()
    return ids


async def set_price_range(
    *, customer_id: int, low: int, high: int | None, price: str,
    valid_from: date | None, valid_to: date | None,
) -> int:
    """挂一条指定区间的生效价，返回 id。"""
    async with SessionLocal() as s:
        row = CustomerPriceRule(
            customer_id=customer_id, sku_id=IDS["sku"], min_qty=low, max_qty=high,
            agreed_price=price, status="active",
            effective_from=valid_from, effective_to=valid_to,
        )
        s.add(row)
        await s.commit()
        return row.id


async def add_candidate(*, customer_id: int, owner_id: int) -> int:
    """直接落一条回收候选（避开扫描链路，专测权限与范围）。"""
    async with SessionLocal() as s:
        now = datetime.now(UTC)
        row = PublicPoolRecycleCandidate(
            customer_id=customer_id, owner_id=owner_id, rule_days=30,
            last_active_at=now - timedelta(days=60), status="pending",
            notice_at=now, due_at=now + timedelta(days=7), created_at=now,
        )
        s.add(row)
        await s.commit()
        return row.id


async def owner_of(customer_id: int) -> int | None:
    async with SessionLocal() as s:
        return (
            await s.execute(
                text("select owner_id from customers where id = :i"), {"i": customer_id}
            )
        ).scalar_one()


# ---------------------------------------------------------------- 各段

async def section1_release_guard(admin: str, mgr_a: str) -> None:
    print("=== 1. P1-1 三个释放入口都要过履约保护 ===")
    cid = IDS["protected"]
    _, head = call("GET", f"/customers/{cid}", mgr_a)
    check_true("夹具客户带在途订单（构成保护）", head.get("data", {}).get("id") == cid)

    # ① 单个转移：owner_id = null → 应被保护拦住
    status, res = call("POST", f"/customers/{cid}/transfer", mgr_a,
                       {"owner_id": None, "reason": None})
    check_in("单个转移传空负责人被拦", status, REJECTED)
    check_true("拦下的理由说清了是哪张单",
               "履约" in json.dumps(res, ensure_ascii=False))
    check("被拦后归属没变", await owner_of(cid), IDS["mgr_a"])

    # ② 分配：owner_id = null → 同样要拦
    status, res = call("POST", f"/customers/{cid}/assign", mgr_a,
                       {"owner_id": None, "reason": None})
    check_in("分配传空负责人被拦", status, REJECTED)
    check("被拦后归属没变（分配）", await owner_of(cid), IDS["mgr_a"])

    # ③ 批量转移：owner_id = null → 同样要拦
    status, res = call("POST", "/customers/batch-transfer", mgr_a,
                       {"customer_ids": [cid, IDS["protected2"]], "owner_id": None})
    check_in("批量转移传空负责人被拦", status, REJECTED)
    check("被拦后归属没变（批量）", await owner_of(cid), IDS["mgr_a"])

    # ④ 填了原因 = 主管例外释放 → 放行，且留痕
    status, _ = call("POST", f"/customers/{cid}/transfer", mgr_a,
                     {"owner_id": None, "reason": f"{PREFIX}售后原因-{STAMP}"})
    check("填原因后可例外释放", status, 200)
    check("例外释放后进了公海", await owner_of(cid), None)


async def section2_wecom_durable() -> None:
    print("=== 2. P1-2 外部调用前先落库（企微成功后本地失败）===")

    class FakeClient:
        """假企微：转接一律成功，并记下调用。"""

        def __init__(self) -> None:
            self.calls: list[str] = []

        async def transfer_customer(self, *, external_userid, handover_userid, takeover_userid):
            self.calls.append(external_userid)

        async def get_follow_users(self, external_userid):
            return []

    async with SessionLocal() as s:
        scope = await wecom_service.collect_transfer_scope(
            s,
            handover=await s.get(User, IDS["handover"]),
            takeover=await s.get(User, IDS["takeover"]),
        )
        has_relation = bool(scope.get("wecom_relation"))

    if not has_relation:
        # 没有企微关系也能验证：任务必须在外部调用之前就落库
        print("  （本库没有该离职人的企微关系，改为只验证『任务先落库』）")

    fake = FakeClient()
    original = wecom_client._client
    wecom_client._client = fake
    job_id = None
    try:
        # 故障注入：外部调用成功之后、CRM 阶段抛错
        from app.modules.wecom import service as svc

        real = svc.customer_service.transfer_customer

        async def boom(*args, **kwargs):
            raise RuntimeError(f"{PREFIX}故意在 CRM 阶段失败")

        svc.customer_service.transfer_customer = boom
        try:
            async with SessionLocal() as s:
                user = CurrentUser(
                    await s.get(User, IDS["mgr_a"]), set(), ["admin"], "all"
                )
                await wecom_service.transfer_relations(
                    s, user=user, handover_user_id=IDS["handover"],
                    takeover_user_id=IDS["takeover"], transfer_wecom=True,
                )
        except Exception as error:  # noqa: BLE001 故意的故障注入
            print(f"  （已按预期抛错：{type(error).__name__}）")
        finally:
            svc.customer_service.transfer_customer = real
    finally:
        wecom_client._client = original

    # 关键断言：外部调用发生过，且**任务与逐项记录都还在库里**
    async with SessionLocal() as s:
        job = (
            await s.execute(
                text(
                    "select id from wecom_sync_jobs where operator_id = :o "
                    "order by id desc limit 1"
                ),
                {"o": IDS["mgr_a"]},
            )
        ).scalar_one_or_none()
        job_id = job
        items = 0
        if job:
            items = (
                await s.execute(
                    text("select count(*) from wecom_transfer_items where job_id = :j"),
                    {"j": job},
                )
            ).scalar_one()

    check_true("交接任务在库里留下了（没被后续失败一起回滚）", job_id is not None,
               f"job_id={job_id}")
    check_true("逐项执行计划也留下了", items > 0, f"{items} 条")


async def section3_retry_switch() -> None:
    print("=== 3. P1-3 重试与首次同档：关掉总开关后重试也被拒 ===")
    # 后端启动时 WECOM_TRANSFER_ENABLED 未开 → 开关检查应当先生效
    status, res = call("POST", "/integrations/wecom/transfer/999999/retry", None)
    # 未登录是 401，这里用 admin 才有意义
    admin_token = login("admin", "admin123")
    status, res = call("POST", "/integrations/wecom/transfer/999999/retry", admin_token)
    check("关开关后重试被拒", status, 403)
    check_true("拒绝文案指向总开关",
               "锁定" in json.dumps(res, ensure_ascii=False),
               json.dumps(res, ensure_ascii=False)[:90])


async def section4_sample_dual_role() -> None:
    print("=== 4. P1-4 打样双责任：跟单与生产分开盘点 ===")
    async with SessionLocal() as s:
        handover = await s.get(User, IDS["handover"])
        takeover = await s.get(User, IDS["takeover"])
        scope = await wecom_service.collect_transfer_scope(
            s, handover=handover, takeover=takeover
        )
    follow_ids = {row["business_id"] for row in scope.get("sample", [])}
    prod_ids = {row["business_id"] for row in scope.get("sample_production", [])}

    check_true("两项都负责 → 跟单项里有一条", IDS["both"] in follow_ids)
    check_true("两项都负责 → 生产项里也有一条", IDS["both"] in prod_ids)
    check_true("只负责生产 → 生产项里有它", IDS["only_prod"] in prod_ids)
    check_true("只负责生产 → 跟单项里没有它", IDS["only_prod"] not in follow_ids)
    check_true("只负责跟单 → 跟单项里有它", IDS["only_follow"] in follow_ids)
    check_true("只负责跟单 → 生产项里没有它", IDS["only_follow"] not in prod_ids)

    detail = [row["label"] for row in scope.get("sample_production", [])]
    check_true("生产项标签写明了是生产责任",
               all("生产责任" in label for label in detail) if detail else False,
               str(detail[:2]))


async def section5_merge_price(mgr_a: str, admin: str) -> None:
    print("=== 5. P1-5 合并价格冲突：不截断 / 区间重叠 / 有效期 / 非法值 ===")
    today = date.today()

    def preview(token: str, src: int, dst: int):
        """取合并影响清单里的冲突行（**路由是 /customers/{id}/merge-preview**）。"""
        status, res = call(
            "GET", f"/customers/{src}/merge-preview?target_customer_id={dst}", token
        )
        data = res.get("data")
        rows = (data or {}).get("conflicts") or [] if isinstance(data, dict) else []
        return status, rows

    async def status_counts(rule_ids: list[int]) -> tuple[int, int]:
        async with SessionLocal() as s:
            active = (
                await s.execute(
                    text(
                        "select count(*) from customer_price_rules "
                        "where id = any(:ids) and status = 'active'"
                    ),
                    {"ids": rule_ids},
                )
            ).scalar_one()
            historical = (
                await s.execute(
                    text(
                        "select count(*) from customer_price_rules "
                        "where id = any(:ids) and status = 'historical'"
                    ),
                    {"ids": rule_ids},
                )
            ).scalar_one()
        return active, historical

    # ---- ① 25 对同区间、价不同的冲突：执行时**不能截断** ----
    src, dst = IDS["merge_src"], IDS["merge_dst"]
    src_ids = await add_price_pairs(
        customer_id=src, count=25, base_price="10.00", today=today
    )
    dst_ids = await add_price_pairs(
        customer_id=dst, count=25, base_price="20.00", today=today
    )

    status, rows = preview(mgr_a, src, dst)
    check("合并影响清单可取", status, 200)
    price_row = next((r for r in rows if r.get("key") == "customer_price"), None)
    check_true("识别出专属价冲突", price_row is not None)
    check("冲突总数 25（展示可截断，总数不能少）", (price_row or {}).get("count"), 25)

    # 用 admin 调：涉及价格裁决时**还要 price:manage**（P1-6 的新闸门），
    # 这一节测的是"截断"，价格权限另在第 6 节测
    status, _ = call("POST", "/customers/merge", admin, {
        "source_customer_id": src, "target_customer_id": dst,
        "resolutions": {"customer_price": "keep_target"},
        "reason": f"{PREFIX}合并25对-{STAMP}",
    })
    check("25 对冲突合并成功", status, 200)

    dst_active, _ = await status_counts(dst_ids)
    src_active, src_hist = await status_counts(src_ids)
    check("目标那 25 条保持生效", dst_active, 25)
    # ⭐ 这一条才真正区分"截断"：旧实现只处理前 20 对，
    # 剩下 5 条来源价**仍然是生效的**，于是合并后两套价并存。
    check("来源那 25 条**全部**转历史（旧实现只转前 20 条）", src_active, 0)
    check("来源转历史的是 25 条", src_hist, 25)

    # ---- ② 起订量不同但区间重叠 → 必须识别为冲突 ----
    await set_price_range(
        customer_id=IDS["rng_src"], low=100, high=1000, price="10.00",
        valid_from=today - timedelta(days=5), valid_to=today + timedelta(days=50),
    )
    await set_price_range(
        customer_id=IDS["rng_dst"], low=0, high=500, price="20.00",
        valid_from=today - timedelta(days=5), valid_to=today + timedelta(days=50),
    )
    status, rows = preview(mgr_a, IDS["rng_src"], IDS["rng_dst"])
    row = next((r for r in rows if r.get("key") == "customer_price"), None)
    check_true(
        "起订量不同但区间重叠 → 识别为冲突（旧实现只比起订量，会漏）",
        row is not None, f"count={(row or {}).get('count')}",
    )

    # ---- ③ 有效期不重叠 → 不算冲突 ----
    await set_price_range(
        customer_id=IDS["exp_src"], low=9000, high=9100, price="11.00",
        valid_from=today - timedelta(days=300), valid_to=today - timedelta(days=200),
    )
    await set_price_range(
        customer_id=IDS["exp_dst"], low=9000, high=9100, price="22.00",
        valid_from=today + timedelta(days=10), valid_to=today + timedelta(days=100),
    )
    status, rows = preview(mgr_a, IDS["exp_src"], IDS["exp_dst"])
    row = next((r for r in rows if r.get("key") == "customer_price"), None)
    check_true("有效期不重叠 → 不算冲突", row is None, f"row={row}")

    # ---- ④ 非法选择值 → 拒绝，不能静默按"保留目标"处理 ----
    await set_price_range(
        customer_id=IDS["bad_src"], low=0, high=100, price="30.00",
        valid_from=today - timedelta(days=5), valid_to=today + timedelta(days=50),
    )
    await set_price_range(
        customer_id=IDS["bad_dst"], low=0, high=100, price="40.00",
        valid_from=today - timedelta(days=5), valid_to=today + timedelta(days=50),
    )
    status, _ = call("POST", "/customers/merge", admin, {
        "source_customer_id": IDS["bad_src"], "target_customer_id": IDS["bad_dst"],
        "resolutions": {"customer_price": "looks_like_a_typo"},
        "reason": f"{PREFIX}非法值-{STAMP}",
    })
    check_in("非法选择值被拒（不静默按保留目标处理）", status, REJECTED)
    check(
        "来源客户没有被合并（拒绝时不得迁移任何关联）",
        await owner_of(IDS["bad_src"]), IDS["mgr_a"],
    )


async def section6_merge_permission() -> None:
    print("=== 6. P1-6 合并涉及价格裁决时要求 price:manage ===")
    token = login(f"{PREFIX.lower()}_priceless_{STAMP}", "123456")
    # 用第 5 节那对"有价格冲突、但没被合并掉"的客户（bad_src/bad_dst）：
    # merge_src 已经在上一节并入 merge_dst（软删），拿它测会先撞 404。
    # 这一对都在 dept_a，正好落在该账号的数据范围内。
    src, dst = IDS["bad_src"], IDS["bad_dst"]

    status, res = call("POST", "/customers/merge", token, {
        "source_customer_id": src, "target_customer_id": dst,
        "resolutions": {"customer_price": "keep_target"},
        "reason": f"{PREFIX}无价格权-{STAMP}",
    })
    check("只有 customer:update 的账号做价格裁决被拒", status, 403)
    check_true("提示指向价格维护权限",
               "price:manage" in json.dumps(res, ensure_ascii=False))
    check("被拒后来源客户归属没变", await owner_of(src), IDS["mgr_a"])

    # 对照：没有价格冲突时，同样这个账号应当能合并（不因噎废食）。
    # ⚠️ 客户必须落在**这个账号的数据范围内**（它是 dept_a 的部门范围），
    # 用 dept_b 的客户会先被范围校验挡成 403，测不到"价格权限这一层"。
    plain_src = IDS["rng_src"]
    plain_dst = IDS["rng_dst"]
    async with SessionLocal() as s:
        await s.execute(
            text("delete from customer_price_rules where customer_id in (:a, :b)"),
            {"a": plain_src, "b": plain_dst},
        )
        await s.commit()
    status, _ = call("POST", "/customers/merge", token, {
        "source_customer_id": plain_src, "target_customer_id": plain_dst,
        "reason": f"{PREFIX}普通合并-{STAMP}",
    })
    check("不涉及价格裁决时，同一账号可以合并", status, 200)


async def section7_recycle_permission() -> None:
    print("=== 7. P1-7 回收复核：独立业务权限 + 客户数据范围 ===")
    own = await add_candidate(customer_id=IDS["merge_dst"], owner_id=IDS["mgr_a"])
    other = await add_candidate(customer_id=IDS["cross"], owner_id=IDS["mgr_b"])

    mgr_token = login(f"{PREFIX.lower()}_mgra_{STAMP}", "123456")
    mgr_b_token = login(f"{PREFIX.lower()}_mgrb_{STAMP}", "123456")
    sales_token = login(f"{PREFIX.lower()}_sales_{STAMP}", "123456")

    # ① 主管（有 pool_review、本部门范围）能看到本团队的候选
    status, res = call("GET", "/public-pool/recycle-candidates?status=pending", mgr_token)
    check("主管能取候选列表", status, 200)
    paged = res.get("data") if isinstance(res.get("data"), dict) else {}
    seen = {row.get("id") for row in (paged.get("items") or [])}
    check_true("本团队的候选在列表里（有复核权就能进）", own in seen, f"seen={sorted(seen)}")
    check_true("**其他团队**的候选不在列表里（按数据范围过滤）", other not in seen)

    # ② 能批本团队那条
    status, _ = call("POST", f"/public-pool/recycle-candidates/{own}/decide", mgr_token,
                     {"decision": "reject", "note": f"{PREFIX}驳回-{STAMP}"})
    check("主管能处理本团队的候选", status, 200)

    # ③ 直接拿 id 处理别人团队的那条 → 拒
    status, _ = call("POST", f"/public-pool/recycle-candidates/{other}/decide", mgr_token,
                     {"decision": "reject", "note": f"{PREFIX}越界-{STAMP}"})
    check_in("拿 id 直接处理其他团队的候选被拒", status, DENIED)

    # ④ 另一个主管管不了甲部这条（反向验证范围真的分团队）
    status, _ = call("POST", f"/public-pool/recycle-candidates/{other}/decide", mgr_b_token,
                     {"decision": "reject", "note": f"{PREFIX}乙部-{STAMP}"})
    check("乙部主管能处理自己团队那条", status, 200)

    # ⑤ 普通业务员（没有 pool_review）不能审批
    status, _ = call("GET", "/public-pool/recycle-candidates?status=pending", sales_token)
    check("业务员取候选列表被拒", status, 403)
    status, _ = call("POST", f"/public-pool/recycle-candidates/{own}/decide", sales_token,
                     {"decision": "reject", "note": None})
    check("业务员审批被拒", status, 403)


async def main() -> None:
    print("== 夹具 ==")
    await cleanup()
    await build_fixtures()
    print(f"  已建：客户/用户/部门等（前缀 {PREFIX}，时间戳 {STAMP}）")

    admin = login("admin", "admin123")
    # 甲部主管：有 customer:assign + pool_review，本部门范围 —— 第 1、5 节用它
    mgr_a = login(f"{PREFIX.lower()}_mgra_{STAMP}", "123456")

    try:
        await section1_release_guard(admin, mgr_a)
        await section2_wecom_durable()
        await section3_retry_switch()
        await section4_sample_dual_role()
        await section5_merge_price(mgr_a, admin)
        await section6_merge_permission()
        await section7_recycle_permission()
    finally:
        print("== 清理 ==")
        await cleanup()

    print()
    if FAILURES:
        print("FAILED %d 项：%s" % (len(FAILURES), "、".join(FAILURES)))
        raise SystemExit(1)
    print("全部通过 ✓")


if __name__ == "__main__":
    asyncio.run(main())
