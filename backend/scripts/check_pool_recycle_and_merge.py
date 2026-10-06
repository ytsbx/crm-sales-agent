"""第六批 · 批次二回归：撞单裁定 / 公海回收预告 / 履约保护（返工单 6.5 / 6.3 / 6.4）。

**只在隔离库跑**：库名必须含 test（或 CI=true），且推送开关全关。

## 覆盖的三组口径

**6.5 撞单裁定**
- 裁定复用普通转移那条路径 → **未完成待办跟着走**，已完成的不动；
- 并发查重不会开出两张相同未决案件（A/B 与 B/A 视为同一对）；
- 并发裁定只有一个成功；重复请求不重复改归属；
- 保存裁定前后的归属（`before_owners` + `resolved_owner_id`）。

**6.3 公海回收预告**
- 扫描**只提名、不改归属**：命中后客户仍归原负责人；
- 未经批准不会进公海；
- 批准前新增履约事项 → 执行时**重新拦截**；
- 主管例外释放必须填原因，且留下完整记录；
- 恢复保留原回收记录；客户已被他人合法领取时不静默覆盖，报冲突；
- 调度重跑不重复提名。

**6.4 履约保护名单**
- 只有草稿 / 已拒绝的报价**不构成保护**；正式发出且在有效期内的才保护；
- 客户已确认接受（`accepted`）的打样**不再保护**；仅签收未确认、未通过仍在复样的继续保护；
- 一张打样结束不影响客户**其他**履约事项的保护；
- 保护原因能说清是哪张单据拦住的。

跑法（需要后端在跑）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_sales_agent_test \\
      PYTHONPATH=. .venv/bin/python scripts/check_pool_recycle_and_merge.py
"""

import asyncio
import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from app.core.database import SessionLocal

FAILURES: list[str] = []
BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000/api/v1")
PREFIX = "CHKREC"


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
    """自底向上清干净。本套件会写：客户/线索/报价/打样/订单/任务/候选/裁定单/历史。"""
    async with SessionLocal() as s:
        cust = "(select id from customers where name like :p)"
        for sql in (
            "delete from public_pool_recycle_candidates where customer_id in " + cust,
            "delete from customer_duplicate_cases where customer_id in " + cust
            + " or candidate_id in " + cust,
            "delete from customer_owner_history where customer_id in " + cust,
            "delete from quotes where customer_id in " + cust,
            "delete from sample_requests where customer_id in " + cust,
            "delete from tasks where customer_id in " + cust,
            "delete from receivable_plans where order_id in "
            "(select id from sales_orders where customer_id in " + cust + ")",
            "delete from sales_orders where customer_id in " + cust,
            "delete from audit_logs where business_type in "
            "('public_pool_candidate','public_pool_rule','customer_duplicate_case') "
            "and after_data::text like :m",
            "delete from public_pool_rules where level like :r",
            "delete from customers where name like :p",
            "delete from leads where name like :p",
            "delete from user_roles where user_id in (select id from users where username like :u)",
            "delete from user_roles where role_id in (select id from roles where code like :r)",
            "delete from role_permissions where role_id in (select id from roles where code like :r)",
            "delete from roles where code like :r",
            "delete from users where username like :u",
        ):
            await s.execute(
                text(sql),
                {"p": f"{PREFIX}%", "m": f"%{PREFIX}%", "u": f"{PREFIX.lower()}%",
                 "r": f"{PREFIX}%"},
            )
        await s.commit()


async def _assert_locks(*, session_factory, case_id: int) -> None:
    """确定性证明"裁定确实取了行锁"。

    做法：先在**独立引擎**（独立连接 + NullPool）的事务里
    `SELECT ... FOR UPDATE` 锁住那张案件，不提交；然后发一次裁定请求量耗时；
    1.2 秒后释放。真取了锁 → 请求会被卡住（耗时 ≥ 1.1 秒）。

    ⚠️ 两个必须注意的点（都实测踩过）：
    ① **不能复用应用的全局 engine**：它在主事件循环里创建，拿到子线程的
       `asyncio.run()` 里用会挂在"连接绑定到另一个 loop"，而**异常在子线程里抛、
       完全静默** —— 会量出 0.02 秒，得出"没加锁"的相反结论；
    ② 子线程的异常必须自己 catch 存起来，否则 `thread.join()` 一个字都不说。
    """
    from sqlalchemy import text as _text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import settings

    # 持锁 2.5 秒、判据放在 1.5 秒：要的是"明显被卡住"。
    # 别把阈值卡到跟持锁时长一样紧 —— 轮询探测就绪（每 0.1 秒一次）和请求建立
    # 本身要花掉零点几秒，实测会量到 0.92 秒而误报（锁其实生效了）。
    # 有锁 ≈ 1.5 秒以上，没锁 ≈ 0.02 秒，这个间隔足够判。
    HOLD = 2.5
    state = {"ready": False, "error": None}

    def holder():
        engine = create_async_engine(settings.database_url, poolclass=NullPool)

        async def _run():
            async with engine.connect() as conn:
                await conn.execute(
                    _text("select id from customer_duplicate_cases where id = :c for update"),
                    {"c": case_id},
                )
                state["ready"] = True
                await asyncio.sleep(HOLD)
                await conn.rollback()

        try:
            asyncio.run(_run())
        except Exception as exc:  # noqa: BLE001 - 子线程异常必须自己抓
            state["error"] = repr(exc)
        finally:
            asyncio.run(engine.dispose())

    t = threading.Thread(target=holder)
    t.start()
    for _ in range(50):
        if state["ready"]:
            break
        time.sleep(0.1)
    if state["error"] or not state["ready"]:
        check_true("（前置）持锁线程就绪", False, str(state["error"]))
        return

    token = login("admin", "admin123")
    started = time.monotonic()
    await asyncio.to_thread(
        call, "POST", f"/customer-duplicate-cases/{case_id}/resolve", token,
        {"decision": "keep_both", "remark": f"{PREFIX}锁验证"},
    )
    elapsed = round(time.monotonic() - started, 2)
    t.join()
    check_true(
        f"裁定请求被行锁挡住 {elapsed} 秒（锁持有 {HOLD} 秒）→ 确实取了 FOR UPDATE",
        elapsed >= 1.5,
        f"耗时 {elapsed} 秒（没加锁时约 0.02 秒）",
    )


async def main() -> int:
    # 先把整套模型加载进来：只 import 用到的那几个模型时，SQLAlchemy 的 metadata
    # 不完整，flush 会因为"外键指向一张没注册的表"报 NoReferencedTableError
    # （实测踩到：`sample_requests.opportunity_id` 找不到 `opportunities`）。
    import app.main
    _ = app.main

    from app.core.security import hash_password
    from app.modules.customer.model import Customer, CustomerDuplicateCase
    from app.modules.quote.model import Quote
    from app.modules.sample.model import SampleRequest
    from app.modules.settings.model import PublicPoolRecycleCandidate, PublicPoolRule
    from app.modules.task.model import Task
    from app.modules.user.model import Role, User, role_permissions, user_roles

    stamp = int(time.time())
    await cleanup()
    admin_token = login("admin", "admin123")
    pwd = hash_password("123456")

    async with SessionLocal() as s:
        a = User(name=f"{PREFIX}甲-{stamp}", username=f"{PREFIX.lower()}_a_{stamp}",
                 password_hash=pwd, status="active")
        b = User(name=f"{PREFIX}乙-{stamp}", username=f"{PREFIX.lower()}_b_{stamp}",
                 password_hash=pwd, status="active")
        s.add_all([a, b])
        await s.flush()
        a_id, b_id = a.id, b.id
        # 给了 customer:assign 的角色，保证 403/401 之类不会掩盖真正的业务拒绝
        role = Role(code=f"{PREFIX}R{stamp}", name=f"{PREFIX}探针角色",
                    data_scope="all")
        s.add(role)
        await s.flush()
        for code in ("customer:view", "customer:assign", "settings:manage"):
            pid = (await s.execute(
                text("select id from permissions where code = :c"), {"c": code}
            )).scalar_one()
            await s.execute(role_permissions.insert().values(
                role_id=role.id, permission_id=pid))
        for u in (a, b):
            await s.execute(user_roles.insert().values(user_id=u.id, role_id=role.id))

        # 长期没跟进（活跃时钟靠 last_progress_at 也会算，所以两个都置旧）
        stale = datetime.now(UTC) - timedelta(days=400)

        # ---- 6.5 撞单夹具：同名同域名的两家 ----
        # 命名要保证**查重真的命中**：打分权重 = 名称包含 55 + 域名相同 30 = 85，
        # 高于默认阈值 50。所以让短名是长名的**子串**（写成"XX宏远" / "XX宏远有限公司"），
        # 不能带中间的流水号 —— 那样两边互不包含，只剩域名 30 分，压根不构成疑似。
        dup_base = f"{PREFIX}宏远{stamp}"
        dup_existing = Customer(name=dup_base, domain=f"{PREFIX.lower()}.example.com",
                                owner_id=a_id, status="active", pool_status="private")
        dup_incoming = Customer(name=f"{dup_base}有限公司", domain=f"{PREFIX.lower()}.example.com",
                                owner_id=b_id, status="active", pool_status="private")

        # ---- 6.3 回收候选夹具 ----
        stale_cust = Customer(name=f"{PREFIX}冷落客户-{stamp}", level="Z", owner_id=a_id,
                              status="active", pool_status="private",
                              last_followup_at=stale, last_progress_at=stale)
        # 有在途打样（客户已确认接受）→ **不该**被保护 → 应被提名
        accepted_sample_cust = Customer(name=f"{PREFIX}已接受打样-{stamp}", level="Z",
                                        owner_id=a_id, status="active", pool_status="private",
                                        last_followup_at=stale, last_progress_at=stale)
        # 只有草稿报价 → **不该**被保护 → 应被提名
        draft_quote_cust = Customer(name=f"{PREFIX}草稿报价-{stamp}", level="Z",
                                    owner_id=a_id, status="active", pool_status="private",
                                    last_followup_at=stale, last_progress_at=stale)
        # 有正式发出的有效报价 → 该被保护 → 不提名为候选
        sent_quote_cust = Customer(name=f"{PREFIX}已发报价-{stamp}", level="Z",
                                   owner_id=a_id, status="active", pool_status="private",
                                   last_followup_at=stale, last_progress_at=stale)
        # 仅签收未确认的打样 → 该被保护
        pending_sample_cust = Customer(name=f"{PREFIX}待确认打样-{stamp}", level="Z",
                                       owner_id=a_id, status="active", pool_status="private",
                                       last_followup_at=stale, last_progress_at=stale)
        s.add_all([dup_existing, dup_incoming, stale_cust, accepted_sample_cust,
                   draft_quote_cust, sent_quote_cust, pending_sample_cust])
        await s.flush()
        ids = {
            "dup_existing": dup_existing.id, "dup_incoming": dup_incoming.id,
            "stale": stale_cust.id, "accepted_sample": accepted_sample_cust.id,
            "draft_quote": draft_quote_cust.id, "sent_quote": sent_quote_cust.id,
            "pending_sample": pending_sample_cust.id,
        }

        # 打样：一张客户已确认接受、一张仅签收待确认
        s.add_all([
            SampleRequest(customer_id=accepted_sample_cust.id, owner_id=a_id,
                          status="signed", confirm_status="accepted", version=1),
            SampleRequest(customer_id=pending_sample_cust.id, owner_id=a_id,
                          status="signed", confirm_status="pending", version=1),
        ])
        # 报价：一张草稿、一张已发出且在有效期内
        s.add_all([
            Quote(quote_no=f"{PREFIX}DRAFT{stamp}", customer_id=draft_quote_cust.id,
                  owner_id=a_id, status="draft",
                  valid_until=(datetime.now(UTC) + timedelta(days=30)).date()),
            Quote(quote_no=f"{PREFIX}SENT{stamp}", customer_id=sent_quote_cust.id,
                  owner_id=a_id, status="sent",
                  valid_until=(datetime.now(UTC) + timedelta(days=30)).date()),
        ])
        # 待办：一条未完成、一条已完成（用来验裁定后的责任迁移）
        s.add_all([
            Task(title=f"{PREFIX}未完成待办{stamp}", customer_id=dup_existing.id,
                 owner_id=a_id, status="pending"),
            Task(title=f"{PREFIX}已完成待办{stamp}", customer_id=dup_existing.id,
                 owner_id=a_id, status="done"),
        ])
        rule = PublicPoolRule(level="Z", days=60, enabled=True)
        s.add(rule)
        await s.flush()
        await s.commit()

    b_token = login(f"{PREFIX.lower()}_b_{stamp}", "123456")

    async def owner_of(cid):
        async with SessionLocal() as s:
            return (
                await s.execute(select(Customer.owner_id).where(Customer.id == cid))
            ).scalar_one()

    async def task_owner(cid, status):
        async with SessionLocal() as s:
            return (
                await s.execute(
                    select(Task.owner_id).where(Task.customer_id == cid, Task.status == status)
                )
            ).scalars().first()

    try:
        print("=== 1. 6.4 保护名单：草稿/已接受的打样不再保护，正式报价才保护 ===")
        async with SessionLocal() as s:
            from app.modules.settings import service as settings_service

            detail = await settings_service.protection_detail(s)
        check_true("草稿报价的客户**不在**保护名单", ids["draft_quote"] not in detail,
                   str(detail.get(ids["draft_quote"])))
        check_true("客户已确认接受的打样**不再**保护", ids["accepted_sample"] not in detail,
                   str(detail.get(ids["accepted_sample"])))
        check_true("已发出的有效报价**仍然**保护", ids["sent_quote"] in detail,
                   str(detail.get(ids["sent_quote"])))
        check_true("仅签收未确认的打样**仍然**保护", ids["pending_sample"] in detail,
                   str(detail.get(ids["pending_sample"])))
        check_true("保护原因说清了是哪张单据",
                   any("报价" in r or "QT" in r or "SENT" in r
                       for r in detail.get(ids["sent_quote"], [])),
                   str(detail.get(ids["sent_quote"])))

        print("=== 2. 6.3 扫描只提名、不改归属 ===")
        status, res = call("POST", "/public-pool/run-recycle", admin_token)
        check("扫描接口成功", status, 200)
        data = res.get("data") or {}
        check("返回里写明本轮是「提名」而不是「回收」", data.get("released_count"), 0)
        check_true("冷落客户被提名为候选",
                   any(c["customer_id"] == ids["stale"] for c in (data.get("candidates") or [])),
                   str([c["customer_id"] for c in (data.get("candidates") or [])]))
        check_true("草稿报价的客户也被提名（不再被草稿保护）",
                   any(c["customer_id"] == ids["draft_quote"] for c in (data.get("candidates") or [])),
                   str([c["customer_id"] for c in (data.get("candidates") or [])]))
        check_true("已接受打样的客户也被提名",
                   any(c["customer_id"] == ids["accepted_sample"] for c in (data.get("candidates") or [])),
                   "")
        check_true("有效报价的客户被豁免（没被提名）",
                   not any(c["customer_id"] == ids["sent_quote"] for c in (data.get("candidates") or [])),
                   "")
        check_true("仅签收未确认的打样客户被豁免",
                   not any(c["customer_id"] == ids["pending_sample"] for c in (data.get("candidates") or [])),
                   "")

        check("**提名后客户仍归原负责人**（没有直接回收）", await owner_of(ids["stale"]), a_id)

        async with SessionLocal() as s:
            cand = (
                await s.execute(
                    select(PublicPoolRecycleCandidate).where(
                        PublicPoolRecycleCandidate.customer_id == ids["stale"]
                    )
                )
            ).scalars().first()
            cand_id = cand.id
            check("候选状态是待复核", cand.status, "pending")
            check("候选记着原负责人", cand.owner_id, a_id)
            check("候选记着命中的规则天数", cand.rule_days, 60)
            check_true("候选记着两个活跃时钟",
                       cand.last_contact_at is not None and cand.last_active_at is not None, "")
            check_true("候选带预告到期时间", cand.due_at is not None, "")

        print("=== 3. 6.3 重跑不重复提名 ===")
        status, res2 = call("POST", "/public-pool/run-recycle", admin_token)
        again = (res2.get("data") or {}).get("candidates") or []
        check_true("同一客户不会被重复提名",
                   not any(c["customer_id"] == ids["stale"] for c in again),
                   str([c["customer_id"] for c in again]))
        async with SessionLocal() as s:
            n = int((await s.execute(text(
                "select count(*) from public_pool_recycle_candidates where customer_id = :c"
            ), {"c": ids["stale"]})).scalar_one())
        check("库里这个客户只有一条候选", n, 1)

        print("=== 4. 6.3 批准前新增履约事项 → 执行时重新拦截 ===")
        # 先把预告期走完：扫描刚生成的候选 `due_at` 在 7 天后，
        # 而"等待期没满不许批"是另一条独立的闸门（由 check_sixth_round_p2 覆盖）。
        # 不先走完它，这里会被"还没到可回收时间"拦下，就测不到本节要说的事
        # —— 预告之后客户又有新履约事项时，执行前会**再查一遍**并说清是哪张单。
        async with SessionLocal() as s:
            await s.execute(
                text(
                    "update public_pool_recycle_candidates "
                    "set due_at = now() - interval '1 day' where id = :i"
                ),
                {"i": cand_id},
            )
            await s.commit()
        # 预告发出后，客户又被报了价（正式发出、有效）→ 执行应当被拦
        async with SessionLocal() as s:
            s.add(Quote(quote_no=f"{PREFIX}LATE{stamp}", customer_id=ids["stale"],
                        owner_id=a_id, status="sent",
                        valid_until=(datetime.now(UTC) + timedelta(days=30)).date()))
            await s.commit()
        status, res = call(
            "POST", f"/public-pool/recycle-candidates/{cand_id}/decide", admin_token,
            {"decision": "approve"},
        )
        check("批准被拦下（预告后出现了新的有效报价）", status, 422)
        check_true("拒绝理由里说清了是哪张单据拦住的",
                   "报价" in str(res.get("message")), str(res.get("message"))[:80])
        check("被拦后客户**仍归原负责人**", await owner_of(ids["stale"]), a_id)
        async with SessionLocal() as s:
            still = (await s.execute(select(PublicPoolRecycleCandidate.status).where(
                PublicPoolRecycleCandidate.id == cand_id))).scalar_one()
        check("被拦后候选仍在待复核（没被吞掉）", still, "pending")

        print("=== 5. 6.3 例外释放必须填原因 ===")
        status, res = call("PATCH", f'/customers/{ids["sent_quote"]}', admin_token,
                           {"remark": "无"})
        status, res = call(
            "POST", f'/customers/{ids["sent_quote"]}/release-to-pool', admin_token, {}
        )
        check("普通释放遇履约保护被拦", status, 422)
        check_true("且说清是哪张单拦住的", "报价" in str(res.get("message")),
                   str(res.get("message"))[:80])
        check("被拦后归属没变", await owner_of(ids["sent_quote"]), a_id)

        status, res = call(
            "POST", f'/customers/{ids["sent_quote"]}/release-to-pool', admin_token,
            {"reason": f"{PREFIX}主管特批：客户已转为竞品，不再跟"},
        )
        check("主管填了原因可以例外释放", status, 200)
        check("归属已清空（进了公海）", await owner_of(ids["sent_quote"]), None)
        async with SessionLocal() as s:
            hit = (await s.execute(text(
                "select count(*) from audit_logs where action = 'pool_release_exception' "
                "and business_id = :c and after_data::text like :m"
            ), {"c": ids["sent_quote"], "m": f"%{PREFIX}主管特批%"})).scalar_one()
        check_true("例外释放留了完整记录（含原因与保护事项）", int(hit) >= 1, f"{hit} 条")

        print("=== 6. 6.3 批准执行 + 恢复 ===")
        # 先清掉刚加的报价，让这条候选重新具备执行条件
        async with SessionLocal() as s:
            await s.execute(text(
                "delete from quotes where customer_id = :c and quote_no like :n"
            ), {"c": ids["stale"], "n": f"{PREFIX}LATE%"})
            await s.commit()
        status, res = call(
            "POST", f"/public-pool/recycle-candidates/{cand_id}/decide", admin_token,
            {"decision": "approve"},
        )
        check("清掉新报价后批准成功", status, 200)
        check("客户已进公海", await owner_of(ids["stale"]), None)
        async with SessionLocal() as s:
            executed = (await s.execute(select(PublicPoolRecycleCandidate).where(
                PublicPoolRecycleCandidate.id == cand_id))).scalars().first()
            check("候选状态转为已回收", executed.status, "executed")

        status, res = call(
            "POST", f"/public-pool/recycle-candidates/{cand_id}/restore", admin_token,
            {"note": f"{PREFIX}误回收，恢复"},
        )
        check("恢复成功", status, 200)
        check("客户回到原负责人名下", await owner_of(ids["stale"]), a_id)
        async with SessionLocal() as s:
            restored = (await s.execute(select(PublicPoolRecycleCandidate).where(
                PublicPoolRecycleCandidate.id == cand_id))).scalars().first()
            check("**原回收记录保留**（状态转已恢复，不删）", restored.status, "restored")
            check_true("恢复了是谁做的、为什么", restored.restored_by is not None
                       and bool(restored.restore_note), "")

        print("=== 7. 6.3 恢复不抢已被合法领取的客户 ===")
        # 先制造一条"已回收、然后被别人领走"的场景。
        # 注意扫描那一轮已经给这个客户提过名了，先清掉，免得出现两条候选。
        async with SessionLocal() as s:
            await s.execute(text(
                "delete from public_pool_recycle_candidates where customer_id = :c"
            ), {"c": ids["draft_quote"]})
            s.add(PublicPoolRecycleCandidate(
                customer_id=ids["draft_quote"], owner_id=a_id, level="Z", rule_days=60,
                status="executed", notice_at=datetime.now(UTC),
                executed_at=datetime.now(UTC), created_at=datetime.now(UTC),
            ))
            cust = await s.get(Customer, ids["draft_quote"])
            cust.owner_id = None
            cust.pool_status = "public"
            await s.commit()
            conflict_case = (await s.execute(select(PublicPoolRecycleCandidate.id).where(
                PublicPoolRecycleCandidate.customer_id == ids["draft_quote"]))).scalar_one()
        # 乙把它领走
        status, res = call("POST", f'/public-pool/customers/{ids["draft_quote"]}/claim', b_token)
        check("（前置）乙合法领取成功", status, 200)
        check("当前负责人是乙", await owner_of(ids["draft_quote"]), b_id)

        status, res = call(
            "POST", f"/public-pool/recycle-candidates/{conflict_case}/restore", admin_token,
            {"note": f"{PREFIX}想还给甲"},
        )
        check("恢复被拒（已被别人领走，不静默覆盖）", status, 409)
        check_true("提示里说清已被谁领取", "领取" in str(res.get("message")),
                   str(res.get("message"))[:80])
        check("**归属没有被抢回来**（仍是乙的）", await owner_of(ids["draft_quote"]), b_id)
        async with SessionLocal() as s:
            conflict_row = (await s.execute(select(PublicPoolRecycleCandidate).where(
                PublicPoolRecycleCandidate.id == conflict_case))).scalars().first()
            check("冲突已记录（留下 who 的 id 供主管协调）",
                  conflict_row.restore_conflict_owner_id, b_id)

        print("=== 8. 6.5 裁定复用统一归属变更：待办跟着走、已完成不动 ===")
        status, res = call("POST", f'/customers/{ids["dup_incoming"]}/duplicate-cases',
                           admin_token)
        check("开出撞单待裁定单", status, 200)
        status, res = call("GET", "/customer-duplicate-cases?status=pending&page_size=50",
                           admin_token)
        items = (res.get("data") or {}).get("items") or []
        case = next((c for c in items
                     if {c["customer_id"], c["candidate_id"]} == {ids["dup_existing"], ids["dup_incoming"]}), None)
        check_true("能在队列里找到这条案件", case is not None, str(len(items)))
        case_id = case["id"]

        # 反向再开一次：同一对不该出现第二条。
        # ⚠️ page_size 上限是 100，写 200 会被参数校验拒掉（返回体里的 data
        # 是**校验错误明细列表**，不是分页结构 —— 拿它当分页读会 AttributeError）。
        call("POST", f'/customers/{ids["dup_existing"]}/duplicate-cases', admin_token)
        status, res = call("GET", "/customer-duplicate-cases?status=pending&page_size=100",
                           admin_token)
        same_pair = [
            c for c in ((res.get("data") or {}).get("items") or [])
            if {c["customer_id"], c["candidate_id"]} == {ids["dup_existing"], ids["dup_incoming"]}
        ]
        check("A/B 与 B/A 视为同一对，只有一条未决案件", len(same_pair), 1)

        status, res = call(
            "POST", f"/customer-duplicate-cases/{case_id}/resolve", admin_token,
            {"decision": "assign_new", "owner_id": b_id, "remark": f"{PREFIX}判给乙"},
        )
        check("裁定成功", status, 200)
        check("归属按裁定改", await owner_of(ids["dup_existing"]), b_id)
        check("**未完成待办跟着新负责人走**", await task_owner(ids["dup_existing"], "pending"), b_id)
        check("**已完成待办的原负责人不动**（历史记录）",
              await task_owner(ids["dup_existing"], "done"), a_id)
        async with SessionLocal() as s:
            row = (await s.execute(select(CustomerDuplicateCase).where(
                CustomerDuplicateCase.id == case_id))).scalars().first()
            check_true("保存了裁定**前**的归属",
                       str(ids["dup_existing"]) in (row.before_owners or {}), str(row.before_owners))
            check("裁定后归属也保存了", row.resolved_owner_id, b_id)

        print("=== 9. 6.5 重复裁定与并发裁定 ===")
        status, res = call(
            "POST", f"/customer-duplicate-cases/{case_id}/resolve", admin_token,
            {"decision": "assign_new", "owner_id": a_id, "remark": f"{PREFIX}再裁一次"},
        )
        check("重复裁定被拒（已裁定过）", status, 422)
        check("归属没有被第二次裁定改回去", await owner_of(ids["dup_existing"]), b_id)

        # 并发：再造一对，两个线程同时对同一案件裁定
        # （命名同样要让短名是长名的子串，否则查重不命中、开不出案件）
        async with SessionLocal() as s:
            race_base = f"{PREFIX}并发{stamp}"
            c1 = Customer(name=race_base, domain=f"{PREFIX}c.example.com",
                          owner_id=a_id, status="active", pool_status="private")
            c2 = Customer(name=f"{race_base}有限公司", domain=f"{PREFIX}c.example.com",
                          owner_id=b_id, status="active", pool_status="private")
            s.add_all([c1, c2])
            await s.flush()
            cc1, cc2 = c1.id, c2.id
            await s.commit()
        from app.modules.customer import duplicates as dup

        async with SessionLocal() as s:
            cases = await dup.open_cases_for_customer(
                s, customer=await s.get(Customer, cc2), source="manual", actor_id=1
            )
            race_case_id = cases[0].id
            await s.commit()

        barrier = threading.Barrier(2)

        def race(target_owner: int):
            barrier.wait()
            return call(
                "POST", f"/customer-duplicate-cases/{race_case_id}/resolve", admin_token,
                {"decision": "assign_new", "owner_id": target_owner,
                 "remark": f"{PREFIX}并发裁定"},
            )

        results = await asyncio.gather(
            asyncio.to_thread(race, a_id),
            asyncio.to_thread(race, b_id),
        )
        codes = sorted(r[0] for r in results)
        check_true("并发裁定只有一个成功",
                   codes.count(200) == 1, f"两个请求分别返回 {codes}")
        async with SessionLocal() as s:
            after = (await s.execute(select(CustomerDuplicateCase).where(
                CustomerDuplicateCase.id == race_case_id))).scalars().first()
            check("案件已结案", after.status, "resolved")
            final_owner = (
                await s.execute(select(Customer.owner_id).where(Customer.id == cc1))
            ).scalar_one()
            check("两条客户都落到同一个负责人名下", final_owner, after.resolved_owner_id)

        print("=== 10. 6.5 队列真分页 ===")
        # 断言方式：**翻页能拿到不同的记录**才是真分页。
        # 不要断言"总数 > 本页"——那取决于本套件造了几条，跟分页实现无关
        # （第一版就这么写错了，total 正好等于 page_size 时白报一条 FAIL）。
        status, res = call("GET", "/customer-duplicate-cases?status=resolved&page=1&page_size=1",
                           admin_token)
        page1 = res.get("data") or {}
        check_true("返回分页结构（items/page/page_size/total）",
                   set(("items", "page", "page_size", "total")) <= set(page1),
                   str(list(page1)))
        check("每页 1 条", len(page1.get("items") or []), 1)
        check_true("总数不少于 2（本套件结了 2 条）", (page1.get("total") or 0) >= 2,
                   str(page1.get("total")))
        first_id = (page1.get("items") or [{}])[0].get("id")

        status, res = call("GET", "/customer-duplicate-cases?status=resolved&page=2&page_size=1",
                           admin_token)
        page2 = res.get("data") or {}
        check("第 2 页也有数据", len(page2.get("items") or []), 1)
        second_id = (page2.get("items") or [{}])[0].get("id")
        check_true("第 2 页拿到的是**另一条**（说明真在翻页，不是被硬顶截断）",
                   bool(first_id) and bool(second_id) and first_id != second_id,
                   f"page1={first_id} page2={second_id}")

        print("=== 11. 确定性验证：裁定/批准确实取了行锁（不靠并发碰运气）===")
        # 并发断言是**概率性**的：两个请求没真正重叠时，即使没加锁也会"看起来对"。
        # 所以再补一条确定性的：先在独立连接的事务里锁住那一行不提交，
        # 再发请求量耗时 —— 真取了行锁就会被卡住。
        # （实测价值：修复前有过 [200,200] 也有过 [200,422]，光靠并发断言守不住。）
        await _assert_locks(session_factory=SessionLocal, case_id=race_case_id)

    finally:
        await cleanup()

    print()
    if FAILURES:
        print(f"FAILED {len(FAILURES)} 项：" + "、".join(FAILURES))
        raise SystemExit(1)
    print("OK 撞单裁定统一归属、回收预告需复核、履约保护按真实状态判定")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
