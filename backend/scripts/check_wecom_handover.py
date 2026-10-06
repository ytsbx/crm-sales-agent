"""第六批 · 批次三回归：离职交接清单与企微交接顺序（返工单 6.6 / 6.7）。

**只在隔离库跑**：库名必须含 test（或 CI=true），且推送开关全关。

## 覆盖的口径

**6.6 交接清单**
- 清单里**有打样单**（原来只有客户/商机/任务/订单/草稿）；
- 只交接**没结束**的打样：已驳回的不算"接手人的活"；
- 交接后接手人**看得到**这张打样（列表/详情按 owner_id 判可见性），
  原负责人不再看得到；
- **只迁离职人的责任**：同一客户下在职同事的未完成待办原样保留；
- 撞单争议中的客户被标出来、不交接。

**6.7 顺序与逐项结果**
- **先检查、后调外部接口**：争议冻结的客户不发出企微转接
  （fake 客户端的调用记录里没有它的 external_userid）；
- 逐项结果分两侧记录（CRM / 企微），失败明细**完整保存**：
  11 条失败就报 11 条（原来在存储阶段截断成 10 条，计数还取截断后的长度）；
- 明细可分页；
- **重试只处理未完成的项**：已经转出去的关系不会再发一遍。

⚠️ 企微调用在**本进程内**用假客户端替换（`wecom_client._client`），
因为"转接"是外部调用，后端进程里没有可注入的替身。预览与明细走 HTTP，
执行与重试直接调 service —— 两边读写的是同一个库，结果一致。

跑法（需要后端在跑）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_sales_agent_test \\
      PYTHONPATH=. .venv/bin/python scripts/check_wecom_handover.py
"""

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from app.core.database import SessionLocal

FAILURES: list[str] = []
BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000/api/v1")
PREFIX = "CHKHO"

#: 归离职人、且能正常转接的关系数（1 条成功 + 11 条失败）
FAILING_RELATIONS = 11


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


class FakeWeComClient:
    """假的企微客户端：只记账 + 按名单抛错，不碰网络。

    `calls` 是这次断言的核心：它记录了**实际发出去**的转接请求 ——
    被冻结的客户有没有偷偷被转出去，看这里最准。
    """

    def __init__(self, fail_userids: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self.fail_userids = set(fail_userids or set())

    async def transfer_customer(self, *, external_userid: str, handover_userid: str,
                                takeover_userid: str) -> dict:
        self.calls.append(external_userid)
        if external_userid in self.fail_userids:
            raise RuntimeError(f"模拟企微拒绝转接：{external_userid}")
        return {}


async def cleanup() -> None:
    """自底向上清干净。本套件会写：客户及其关联、企微三张表、交接任务与逐项结果。"""
    async with SessionLocal() as s:
        cust = "(select id from customers where name like :p)"
        jobs = "(select id from wecom_sync_jobs where detail::text like :m)"
        for sql in (
            "delete from wecom_transfer_items where job_id in " + jobs,
            "delete from wecom_follow_relationships where external_contact_id in "
            "(select id from wecom_external_contacts where external_userid like :p)",
            "delete from wecom_external_contacts where external_userid like :p",
            "delete from wecom_sync_jobs where detail::text like :m",
            "delete from customer_duplicate_cases where customer_id in " + cust
            + " or candidate_id in " + cust,
            "delete from customer_owner_history where customer_id in " + cust,
            "delete from sample_requests where customer_id in " + cust,
            "delete from order_drafts where customer_id in " + cust,
            "delete from sales_orders where customer_id in " + cust,
            "delete from opportunities where customer_id in " + cust,
            "delete from tasks where customer_id in " + cust,
            "delete from customers where name like :p",
            "delete from users where username like :u",
        ):
            await s.execute(
                text(sql), {"p": f"{PREFIX}%", "m": f"%{PREFIX}%", "u": f"{PREFIX.lower()}%"}
            )
        await s.commit()


async def seed_fixtures() -> dict:
    """造一次完整的离职场景。"""
    from app.modules.customer.model import Customer, CustomerDuplicateCase
    from app.modules.opportunity.model import Opportunity
    from app.modules.order.model import OrderDraft, SalesOrder
    from app.modules.sample.model import SampleRequest
    from app.modules.task.model import Task
    from app.modules.user.model import User
    from app.modules.wecom.model import WeComExternalContact, WeComFollowRelationship

    stamp = int(time.time())
    ids: dict = {}

    async with SessionLocal() as s:
        stage_id = (
            await s.execute(text("select id from opportunity_stages order by id limit 1"))
        ).scalar_one()
        admin = (await s.execute(select(User).where(User.username == "admin"))).scalars().first()
        if admin is None:
            raise SystemExit("库里没有 admin 账号，先跑 scripts/seed.py")

        handover = User(
            username=f"{PREFIX.lower()}_out_{stamp}", name=f"{PREFIX}离职人-{stamp}",
            password_hash="x", status="active", wecom_userid=f"{PREFIX}HO{stamp}",
        )
        takeover = User(
            username=f"{PREFIX.lower()}_in_{stamp}", name=f"{PREFIX}接手人-{stamp}",
            password_hash="x", status="active", wecom_userid=f"{PREFIX}TO{stamp}",
        )
        colleague = User(
            username=f"{PREFIX.lower()}_col_{stamp}", name=f"{PREFIX}在职同事-{stamp}",
            password_hash="x", status="active", wecom_userid=f"{PREFIX}COL{stamp}",
        )
        s.add_all([handover, takeover, colleague])
        await s.flush()

        # 正常客户（可交接）
        normal = Customer(
            name=f"{PREFIX}正常客户-{stamp}", owner_id=handover.id,
            status="active", pool_status="private", level="A",
        )
        # 争议客户（撞单未结案 → 自动改派冻结）
        disputed = Customer(
            name=f"{PREFIX}争议客户-{stamp}", owner_id=handover.id,
            status="active", pool_status="private",
        )
        # 冻结判定看的是"有没有未决裁定单"，另一头随便挂一个已存在的客户
        other = Customer(
            name=f"{PREFIX}对家客户-{stamp}", owner_id=colleague.id,
            status="active", pool_status="private",
        )
        s.add_all([normal, disputed, other])
        await s.flush()

        s.add(
            CustomerDuplicateCase(
                customer_id=disputed.id, candidate_id=other.id, score=90,
                evidence={"reasons": ["同名同域"]}, source="import",
                status="pending", created_at=datetime.now(UTC),
            )
        )

        # 打样：一张在途（该交接）、一张已驳回（不该交接）
        open_sample = SampleRequest(
            customer_id=normal.id, owner_id=handover.id, status="approved",
            requested_at=datetime.now(UTC) - timedelta(days=3),
        )
        dead_sample = SampleRequest(
            customer_id=normal.id, owner_id=handover.id, status="rejected",
            requested_at=datetime.now(UTC) - timedelta(days=30),
        )
        s.add_all([open_sample, dead_sample])
        await s.flush()

        # 任务：一张归离职人（该走）、一张归在职同事且挂同一个客户（不该动）
        s.add_all(
            [
                Task(
                    customer_id=normal.id, title=f"{PREFIX}离职人待办-{stamp}",
                    owner_id=handover.id, priority="normal", status="pending", source="manual",
                ),
                Task(
                    customer_id=normal.id, title=f"{PREFIX}同事待办-{stamp}",
                    owner_id=colleague.id, priority="normal", status="doing", source="manual",
                ),
                Opportunity(
                    customer_id=normal.id, title=f"{PREFIX}商机-{stamp}",
                    stage_id=stage_id, currency="CNY", status="active", owner_id=handover.id,
                ),
                SalesOrder(
                    order_no=f"{PREFIX}O{stamp}", customer_id=normal.id,
                    total_amount=100, currency="CNY", status="pending", owner_id=handover.id,
                ),
                OrderDraft(
                    customer_id=normal.id, owner_id=handover.id, source_context={},
                    request_key=f"{PREFIX}-{stamp}", request_hash="b" * 64, created_by=admin.id,
                ),
            ]
        )

        # 企微关系：1 条会成功、11 条会失败、1 条属于争议客户（不该发出）
        contacts: dict[str, WeComExternalContact] = {}
        for index in range(1, 13):
            contact = WeComExternalContact(
                external_userid=f"{PREFIX}EXT{index:02d}-{stamp}",
                crm_customer_id=normal.id, name=f"{PREFIX}外部{index}",
            )
            s.add(contact)
            contacts[f"EXT{index:02d}"] = contact
        frozen_contact = WeComExternalContact(
            external_userid=f"{PREFIX}EXT99-{stamp}",
            crm_customer_id=disputed.id, name=f"{PREFIX}争议外部",
        )
        s.add(frozen_contact)
        await s.flush()

        for contact in list(contacts.values()) + [frozen_contact]:
            s.add(
                WeComFollowRelationship(
                    external_contact_id=contact.id,
                    wecom_userid=handover.wecom_userid,
                    status="active", add_time=datetime.now(UTC), last_sync_at=datetime.now(UTC),
                )
            )

        ids.update(
            {
                "handover": handover.id, "takeover": takeover.id,
                "colleague": colleague.id, "admin": admin.id,
                "normal": normal.id, "disputed": disputed.id,
                "open_sample": open_sample.id, "dead_sample": dead_sample.id,
                "stamp": stamp,
                "ext_ok": f"{PREFIX}EXT01-{stamp}",
                "ext_fail": [f"{PREFIX}EXT{i:02d}-{stamp}" for i in range(2, 13)],
                "ext_frozen": f"{PREFIX}EXT99-{stamp}",
            }
        )
        await s.commit()
    return ids


async def run_transfer_inproc(
    ids: dict, fake: FakeWeComClient, item_assignees: dict[str, int] | None = None
) -> int:
    """在**本进程内**执行一次交接（把企微客户端换成假的），返回任务 id。"""
    from app.core.deps import CurrentUser
    from app.modules.user.model import User
    from app.modules.wecom import client as wecom_client
    from app.modules.wecom import service as wecom_service

    original = wecom_client._client
    wecom_client._client = fake
    try:
        async with SessionLocal() as s:
            admin = await s.get(User, ids["admin"])
            user = CurrentUser(admin, {"wecom:manage"}, ["admin"], "all")
            job = await wecom_service.transfer_relations(
                s,
                user=user,
                handover_user_id=ids["handover"],
                takeover_user_id=ids["takeover"],
                transfer_wecom=True,
                item_assignees=item_assignees,
            )
            job_id = job.id
            await s.commit()
        return job_id
    finally:
        wecom_client._client = original


async def run_retry_inproc(job_id: int, ids: dict) -> dict:
    from app.core.deps import CurrentUser
    from app.modules.user.model import User
    from app.modules.wecom import client as wecom_client
    from app.modules.wecom import service as wecom_service

    original = wecom_client._client
    # 重试时企微"恢复正常"：这一次不该再有失败
    wecom_client._client = FakeWeComClient()
    try:
        async with SessionLocal() as s:
            admin = await s.get(User, ids["admin"])
            user = CurrentUser(admin, {"wecom:manage"}, ["admin"], "all")
            result = await wecom_service.retry_transfer(s, user=user, job_id=job_id)
            await s.commit()
        return result
    finally:
        wecom_client._client = original


async def main() -> int:
    import app.main  # noqa: F401  先把整个应用加载进来（模型注册）

    await cleanup()
    admin_token = login("admin", "admin123")
    ids = await seed_fixtures()
    stamp = ids["stamp"]

    try:
        print("=== 1. 交接清单：打样必须在里面（返工单 6.6）===")
        status, res = call(
            "GET",
            f"/integrations/wecom/transfer-preview?handover_user_id={ids['handover']}"
            f"&takeover_user_id={ids['takeover']}",
            admin_token,
        )
        check("清单可取", status, 200)
        data = res.get("data") or {}
        kinds = {section["kind"] for section in (data.get("sections") or [])}
        check_true("清单里有「打样单」这一类", "sample" in kinds, str(sorted(kinds)))
        for kind in ("customer", "opportunity", "task", "order", "order_draft", "sample"):
            check_true(f"清单覆盖 {kind}", kind in kinds, str(sorted(kinds)))
        check("清单里 1 张在途打样", (data.get("totals") or {}).get("sample"), 1)
        sample_section = next(
            (s for s in (data.get("sections") or []) if s["kind"] == "sample"), None
        )
        sample_labels = [item["label"] for item in (sample_section or {}).get("items", [])]
        check_true(
            "已驳回的打样不在交接清单里（它不是接手人的活）",
            all("已拒绝" not in label for label in sample_labels),
            str(sample_labels),
        )
        check("清单里 1 个客户被冻结", len(data.get("blocked") or []), 1)
        check_true(
            "冻结原因说清了是撞单争议",
            "撞单" in str((data.get("blocked") or [{}])[0].get("blocked_reason")),
        )
        check_true("清单返回了双方姓名", bool((data.get("handover") or {}).get("name")))

        print()
        print("=== 2. 执行交接（1 条转移成功、11 条企微失败）===")
        fake = FakeWeComClient(fail_userids=set(ids["ext_fail"]))
        job_id = await run_transfer_inproc(ids, fake)
        print(f"  交接任务 id={job_id}，实际发出的企微转接 {len(fake.calls)} 条")

        check_true(
            "争议客户的企微转接**没有发出**（先检查后调外部接口）",
            ids["ext_frozen"] not in fake.calls,
            f"calls={fake.calls}",
        )
        check(
            "实际只对未冻结的 12 条关系发出了转接",
            len(fake.calls),
            12,
        )

        async with SessionLocal() as s:
            job = (
                await s.execute(
                    text("select detail from wecom_sync_jobs where id = :i"), {"i": job_id}
                )
            ).scalar_one()
            check("汇总：企微成功 1 条", job.get("wecom_transferred"), 1)
            check("汇总：企微失败 11 条", job.get("wecom_failed"), FAILING_RELATIONS)
            check(
                "汇总：失败计数**不被截断**（原来是 10 条上限）",
                job.get("failures_count"),
                FAILING_RELATIONS,
            )
            check(
                "失败明细完整保存在结果里",
                len(job.get("failures") or []),
                FAILING_RELATIONS,
            )
            check("汇总：1 个客户被冻结", job.get("frozen"), 1)
            check_true(
                "汇总里同时有成功与未完成两类计数",
                job.get("crm_moved", 0) >= 1 and job.get("pending_items", 0) >= 1,
                str({k: job.get(k) for k in ("crm_moved", "pending_items")}),
            )

        print()
        print("=== 3. 打样责任真的交接了（6.6 的核心）===")
        async with SessionLocal() as s:
            sample_owner = (
                await s.execute(
                    text("select owner_id from sample_requests where id = :i"),
                    {"i": ids["open_sample"]},
                )
            ).scalar_one()
            check("在途打样的负责人改成接手人", sample_owner, ids["takeover"])
            dead_owner = (
                await s.execute(
                    text("select owner_id from sample_requests where id = :i"),
                    {"i": ids["dead_sample"]},
                )
            ).scalar_one()
            check("已驳回的打样不动（原负责人保留）", dead_owner, ids["handover"])

            colleague_tasks = (
                await s.execute(
                    text(
                        "select count(*) from tasks where customer_id = :c "
                        "and owner_id = :u and status in ('pending','doing')"
                    ),
                    {"c": ids["normal"], "u": ids["colleague"]},
                )
            ).scalar_one()
            check("在职同事的未完成待办**原样保留**", colleague_tasks, 1)
            moved_tasks = (
                await s.execute(
                    text(
                        "select count(*) from tasks where customer_id = :c and owner_id = :u"
                    ),
                    {"c": ids["normal"], "u": ids["takeover"]},
                )
            ).scalar_one()
            check("离职人的待办跟着走了", moved_tasks, 1)

        # 接手人/原负责人对打样的可见性
        from app.core.deps import CurrentUser
        from app.modules.sample import service as sample_service
        from app.modules.user.model import User

        async with SessionLocal() as s:
            takeover = await s.get(User, ids["takeover"])
            handover_user = await s.get(User, ids["handover"])
            takeover_user = CurrentUser(takeover, {"sample:view"}, ["sales"], "self")
            handover_ctx = CurrentUser(handover_user, {"sample:view"}, ["sales"], "self")
            try:
                await sample_service.get_visible_or_404(s, takeover_user, ids["open_sample"])
                visible = True
            except Exception:
                visible = False
            check_true("接手人能打开这张打样（列表/详情可见）", visible)

            async with SessionLocal() as s2:
                try:
                    await sample_service.get_visible_or_404(s2, handover_ctx, ids["open_sample"])
                    still_visible = True
                except Exception:
                    still_visible = False
            check_true("原负责人已看不到这张打样", not still_visible)

        print()
        print("=== 4. 逐项结果接口：分页 + 只看未完成 ===")
        status, res = call(
            "GET", f"/integrations/wecom/transfer/{job_id}/items?pending_only=true"
            f"&page_size=200", admin_token,
        )
        check("逐项结果可取", status, 200)
        page = res.get("data") or {}
        check(
            "未完成项 = 11 条失败（12 条关系里 1 条成功、1 条冻结被跳过另计）",
            page.get("total"),
            FAILING_RELATIONS,
        )
        status, res = call(
            "GET", f"/integrations/wecom/transfer/{job_id}/items?page=1&page_size=5", admin_token
        )
        page = res.get("data") or {}
        check("分页：第一页 5 条", len(page.get("items") or []), 5)
        check_true("分页：返回了总数", (page.get("total") or 0) > 5, str(page.get("total")))
        items = page["items"]
        check_true(
            "每项带类别中文名与两侧状态",
            all(item.get("kind_label") and item.get("crm_status_label")
                and item.get("wecom_status_label") for item in items),
        )

        status, res = call(
            "GET", f"/integrations/wecom/transfer/{job_id}/items?kind=sample", admin_token
        )
        page = res.get("data") or {}
        check("按类别筛出打样那一项", page.get("total"), 1)

        print()
        print("=== 5. 重试：只做没办完的，已完成的不重发 ===")
        retry = await run_retry_inproc(job_id, ids)
        check("重试了 11 项失败的关系", retry["retried"], FAILING_RELATIONS)
        check("重试后没有剩余未完成项", retry["remaining"], 0)

        status, res = call(
            "GET", f"/integrations/wecom/transfer/{job_id}/items?pending_only=true", admin_token
        )
        check("再看未完成清单：空了", (res.get("data") or {}).get("total"), 0)

        async with SessionLocal() as s:
            rows = (
                await s.execute(
                    text(
                        "select label, wecom_status, attempts from wecom_transfer_items "
                        "where job_id = :j and kind = 'wecom_relation' order by label"
                    ),
                    {"j": job_id},
                )
            ).all()
            by_label = {row[0]: (row[1], row[2]) for row in rows}
            check(
                "第 1 条关系第一次就成功、没被重试第二遍",
                by_label.get(ids["ext_ok"], (None, None))[1],
                1,
            )
            check(
                "失败的 11 条各自被重试过一次（累计 attempts = 2）",
                by_label.get(ids["ext_fail"][0], (None, None))[1],
                2,
            )
            check(
                "冻结那条始终是 skipped（不重试它）",
                by_label.get(ids["ext_frozen"], (None, None))[0],
                "skipped",
            )
            check("重试后全部转接成功", sum(1 for row in rows if row[1] == "transferred"), 12)

        print()
        print("=== 6. 逐项调整接手人（业务方 2026-10-06 定：清单可逐项调）===")
        from app.modules.customer.model import Customer
        from app.modules.sample.model import SampleRequest
        from app.modules.user.model import User

        async with SessionLocal() as s:
            extra_customer = Customer(
                name=f"{PREFIX}分派客户-{stamp}", owner_id=ids["handover"],
                status="active", pool_status="private",
            )
            s.add(extra_customer)
            await s.flush()
            extra_sample = SampleRequest(
                customer_id=extra_customer.id, owner_id=ids["handover"], status="approved",
                requested_at=datetime.now(UTC),
            )
            s.add(extra_sample)
            await s.commit()
            extra_customer_id, extra_sample_id = extra_customer.id, extra_sample.id

        fake2 = FakeWeComClient()
        job2 = await run_transfer_inproc(
            ids,
            fake2,
            item_assignees={
                # 这个客户本身就归在职同事，不跟统一接管人
                f"customer:{extra_customer_id}": ids["colleague"],
                f"sample:{extra_sample_id}": ids["colleague"],
            },
        )
        async with SessionLocal() as s:
            sample_owner = (
                await s.execute(
                    text("select owner_id from sample_requests where id = :i"),
                    {"i": extra_sample_id},
                )
            ).scalar_one()
            check("逐项指定的打样归了指定的人", sample_owner, ids["colleague"])
            cust_owner = (
                await s.execute(
                    text("select owner_id from customers where id = :i"),
                    {"i": extra_customer_id},
                )
            ).scalar_one()
            check("逐项指定的客户也归了指定的人", cust_owner, ids["colleague"])
            rows = (
                await s.execute(
                    text(
                        "select label, to_owner_id from wecom_transfer_items "
                        "where job_id = :j and kind in ('sample','customer')"
                    ),
                    {"j": job2},
                )
            ).all()
            by_label = {row[0]: row[1] for row in rows}
            check_true(
                "逐项结果里记的是**实际**接手人（不是统一那个）",
                by_label.get(f"打样单 #{extra_sample_id}（已批准，待客户确认）") == ids["colleague"]
                or any(value == ids["colleague"] for value in by_label.values()),
                str(by_label),
            )

        # 指定一个停用的人 → 直接拒绝，别把东西丢给一个不能登录的账号
        async with SessionLocal() as s:
            stopped = User(
                username=f"{PREFIX.lower()}_off_{stamp}", name=f"{PREFIX}停用同事-{stamp}",
                password_hash="x", status="disabled", wecom_userid=f"{PREFIX}OFF{stamp}",
            )
            s.add(stopped)
            await s.commit()
            stopped_id = stopped.id
        try:
            await run_transfer_inproc(
                ids, FakeWeComClient(),
                item_assignees={f"customer:{extra_customer_id}": stopped_id},
            )
            rejected = False
        except Exception as error:  # noqa: BLE001  只关心"有没有被拦"
            rejected = "停用" in str(error)
        check_true("指定停用账号当接手人被拒", rejected)

    finally:
        await cleanup()

    print()
    if FAILURES:
        print(f"FAILED：{len(FAILURES)} 项未通过")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
