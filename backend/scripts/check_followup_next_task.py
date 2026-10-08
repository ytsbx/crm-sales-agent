#!/usr/bin/env python
"""补建后续任务：业务关联继承（11.6）与负责人校验（11.7）。

**只在隔离库跑**：库名必须含 test，且推送开关全关、**必须显式给 API_BASE**。

## 两条各守什么

- **11.6**：`POST /followups/{id}/create-next-task` 原本只继承
  客户/联系人/线索/商机 —— 跟进上挂着的**报价、订单、打样全丢了**，
  新任务因此失去"从哪张单子来的"，后续查看、筛选、跳回原单据全都受影响。
  任务表有 `quote_id` / `order_id` 两列，打样**没有对应的列**，改用"来源业务对象"承载。
  每一条关联在继承前都**当场复核**：还在、没被软删、且属于同一个客户 ——
  不为"复制字段"把越权或跨客户的脏关联带进新任务；对不上就不继承并在返回里说明。
- **11.7**：负责人校验原本只看"显式传进来的那个"。历史跟进的负责人早已停用时，
  补建出来的任务会被分给一个停用账号（谁都看不到、也没人处理）。
  现在**显式选的、从跟进继承来的走同一道闸门**。

## 为什么新建套件

`ls scripts/ | grep -i "follow|task"` 零命中，`ops/check_suites.txt` 里也没有 ——
这两条链此前**没有任何套件**。

跑法（隔离库）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_iso_test_xxx \\
      FILE_ROOT=data/iso-files-xxx PYTHONPATH=. .venv/bin/python \\
      scripts/check_followup_next_task.py
"""

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

import app.main  # noqa: F401  保证所有模型都注册进 metadata

_ = app.main  # 显式"用"一下：只 import 不带这一句，pyflakes 会当成未使用

from sqlalchemy import String, delete, func, select

from app.core.audit import AuditLog
from app.core.database import SessionLocal
from app.modules.customer.model import Customer
from app.modules.followup.model import FollowUp
from app.modules.opportunity.model import Opportunity, OpportunityStage
from app.modules.order.model import SalesOrder
from app.modules.quote.model import Quote, QuoteVersion
from app.modules.sample.model import SampleRequest
from app.modules.task.model import Task
from app.modules.user.model import User

if not os.environ.get("API_BASE"):
    raise SystemExit(
        "必须显式设置 API_BASE（不能依赖默认的 8000，那是开发后端）：\n"
        "  API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_iso_test_xxx \\\n"
        "    PYTHONPATH=. .venv/bin/python scripts/check_followup_next_task.py"
    )
BASE = os.environ["API_BASE"].rstrip("/")

MARKER = f"CHKFN{int(time.time())}"
FAILURES: list[str] = []


def check(label: str, actual, expected) -> None:
    """比较式断言（与 `check_business_time_edges.py` 同一签名）。

    项目里两种签名都有（`check_tenth_round_logistics.py` 用的是"条件 + 详情"），
    **同一个文件里不许混用** —— 混用会让 `check(label, 实际值, 期望值)` 被当成
    "条件 = 实际值"，只要实际值非 0 就恒真、变成假绿（本轮在另一个套件里实打实踩过）。
    所以本文件从头到尾只用这一种。
    """
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}：{actual!r}（期望 {expected!r}）')
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
        except Exception:  # noqa: BLE001
            return exc.code, {}


def login(username: str, password: str) -> str:
    _, res = call("POST", "/auth/login", body={"username": username, "password": password})
    if res.get("code") != 0:
        raise SystemExit(f"登录失败（{username}）：{res.get('message')}")
    return res["data"]["access_token"]


async def task_row(task_id: int) -> dict | None:
    async with SessionLocal() as session:
        row = (
            await session.execute(select(Task).where(Task.id == task_id))
        ).scalars().first()
        if row is None:
            return None
        return {
            "customer_id": row.customer_id,
            "contact_id": row.contact_id,
            "lead_id": row.lead_id,
            "opportunity_id": row.opportunity_id,
            "quote_id": row.quote_id,
            "order_id": row.order_id,
            "owner_id": row.owner_id,
            "source_business_type": row.source_business_type,
            "source_business_id": row.source_business_id,
        }


async def main():
    admin = login("admin", "admin123")

    customer_ids: list[int] = []
    followup_ids: list[int] = []
    task_ids: list[int] = []
    stop_user_id: int | None = None

    def api(method, path, body=None, expected=200, token=None):
        status, result = call(method, path, token=token or admin, body=body)
        assert status == expected, (method, path, status, result)
        return result.get("data")

    try:
        # ------------------------------------------------------------ 夹具
        print("=== 0. 夹具 ===")
        async with SessionLocal() as session:
            admin_id = int(
                (await session.execute(select(User.id).where(User.username == "admin"))).scalar_one()
            )
            stage_id = int(
                (
                    await session.execute(
                        select(OpportunityStage.id).order_by(OpportunityStage.id.asc()).limit(1)
                    )
                ).scalar_one()
            )
            mine = Customer(name=f"{MARKER}客户A", owner_id=admin_id)
            other = Customer(name=f"{MARKER}客户B", owner_id=admin_id)
            # 一个**已停用**的员工：用来验"默认继承来的负责人也要查在职"
            stopped = User(
                username=f"chkstop{int(time.time())}",
                name=f"{MARKER}停用员工",
                password_hash="x",
                status="inactive",
            )
            session.add_all([mine, other, stopped])
            await session.flush()
            customer_ids += [mine.id, other.id]
            stop_user_id = stopped.id
            opp = Opportunity(
                customer_id=mine.id,
                title=f"{MARKER}商机",
                stage_id=stage_id,
                owner_id=admin_id,
            )
            session.add(opp)
            await session.flush()
            opportunity_id = opp.id
            # 订单（ORM 造：这里验的不是下单流程）
            order = SalesOrder(
                order_no=f"{MARKER}-ORD",
                customer_id=mine.id,
                owner_id=admin_id,
                sales_owner_id=admin_id,
                total_amount=100,
                currency="CNY",
                status="pending",
                created_by=admin_id,
            )
            session.add(order)
            await session.flush()
            order_id = order.id
            sample = SampleRequest(customer_id=mine.id, status="pending", owner_id=admin_id)
            session.add(sample)
            await session.flush()
            sample_id = sample.id
            await session.commit()
        mine_id, other_id = customer_ids

        # 报价走接口（它有自己的必填与快照逻辑）
        quote_a = api(
            "POST", "/quotes", {"opportunity_id": opportunity_id, "customer_id": mine_id}
        )["quote_id"]
        print(f"  客户 {mine_id}/{other_id}、订单 {order_id}、打样 {sample_id}、报价 {quote_a}")

        # ------------------------------------- 11.6 关联继承
        print("\n=== 1. 补建任务：报价 / 订单 / 打样都带上 ===")
        async with SessionLocal() as session:
            fu = FollowUp(
                customer_id=mine_id,
                opportunity_id=opportunity_id,
                quote_id=quote_a,
                order_id=order_id,
                sample_id=sample_id,
                owner_id=admin_id,
                followup_type="电话",
                content=f"{MARKER}跟进（三个关联齐全）",
            )
            session.add(fu)
            await session.flush()
            fu_id = fu.id
            followup_ids.append(fu_id)
            await session.commit()

        due1 = (datetime.now(UTC) + timedelta(days=3)).isoformat()
        data = api(
            "POST",
            f"/followups/{fu_id}/create-next-task",
            {"title": f"{MARKER}任务1", "due_at": due1},
        )
        task_ids.append(data["task_id"])
        row = await task_row(data["task_id"])
        check("客户继承下来了", row["customer_id"], mine_id)
        check("商机继承下来了", row["opportunity_id"], opportunity_id)
        check("★报价继承下来了（原实现丢掉）", row["quote_id"], quote_a)
        check("★订单继承下来了（原实现丢掉）", row["order_id"], order_id)
        check(
            "★打样记在「来源业务对象」上（任务表没有 sample 列）",
            (row["source_business_type"], row["source_business_id"]),
            ("sample", sample_id),
        )
        check("返回里说明没有丢任何关联", data.get("skipped_references"), [])

        # ------------------------------------- 11.6 跨客户关联不许带过去
        print("\n=== 2. 跟进的关联若不属于同一个客户 → 不继承，并说明 ===")
        # 客户 B 也要有商机才能建报价（报价"必须关联商机"），所以先给它一个
        async with SessionLocal() as session:
            opp_b = Opportunity(
                customer_id=other_id,
                title=f"{MARKER}商机B",
                stage_id=stage_id,
                owner_id=admin_id,
            )
            session.add(opp_b)
            await session.flush()
            opp_b_id = opp_b.id
            await session.commit()
        quote_b_id = api(
            "POST", "/quotes", {"opportunity_id": opp_b_id, "customer_id": other_id}
        )["quote_id"]
        async with SessionLocal() as session:
            fu2 = FollowUp(
                customer_id=mine_id,  # 跟进挂在客户 A
                quote_id=quote_b_id,  # 但关联的是客户 B 的报价（脏关联）
                owner_id=admin_id,
                followup_type="电话",
                content=f"{MARKER}跟进（跨客户脏关联）",
            )
            session.add(fu2)
            await session.flush()
            fu2_id = fu2.id
            followup_ids.append(fu2_id)
            await session.commit()
        data = api(
            "POST",
            f"/followups/{fu2_id}/create-next-task",
            {"title": f"{MARKER}任务2", "due_at": (datetime.now(UTC) + timedelta(days=4)).isoformat()},
        )
        task_ids.append(data["task_id"])
        row = await task_row(data["task_id"])
        check(
            "★跨客户的报价**没有**被带进新任务",
            row["quote_id"],
            None,
        )
        check(
            "并且明确说了是哪一条没带过来（skipped_references 里点出「报价 #…」）",
            any("报价" in str(s) for s in (data.get("skipped_references") or [])),
            True,
        )
        # ------------------------------------- 11.6 重复补建
        print("\n=== 3. 重复补建不产生第二张任务 ===")
        before = await _task_count(f"{MARKER}%")
        status2, res2 = call(
            "POST",
            f"/followups/{fu_id}/create-next-task",
            token=admin,
            # 与第一次**一字不差**（同标题、同到期）：内容一变就会被正确地
            # 当成"另一张任务"而拒绝，那样验的就不是"重复"了
            body={"title": f"{MARKER}任务1", "due_at": due1},
        )
        check("同内容重复提交 → 回放原任务（不是 5xx）", status2, 200)
        check("且返回的是同一张任务", res2.get("data", {}).get("task_id"), task_ids[0])
        check("★没有多出第二张任务", await _task_count(f"{MARKER}%"), before)

        # ------------------------------------- 11.7 负责人校验
        print("\n=== 4. 负责人：默认继承来的也要查在职 ===")
        # ① 默认负责人在职 → 正常。
        #    另造一条跟进：fu2 在第 2 节已经建过后续任务了，对它再来一次会被
        #    正确地判成"已有后续任务"（409），那验的就不是负责人这一条了。
        async with SessionLocal() as session:
            fu4 = FollowUp(
                customer_id=mine_id,
                owner_id=admin_id,
                followup_type="电话",
                content=f"{MARKER}跟进（负责人在职）",
            )
            session.add(fu4)
            await session.flush()
            fu4_id = fu4.id
            followup_ids.append(fu4_id)
            await session.commit()
        data = api(
            "POST",
            f"/followups/{fu4_id}/create-next-task",
            {
                "title": f"{MARKER}任务3",
                "due_at": (datetime.now(UTC) + timedelta(days=5)).isoformat(),
            },
        )
        task_ids.append(data["task_id"])
        check("默认负责人在职 → 建得出来", (await task_row(data["task_id"]))["owner_id"], admin_id)

        # ② 默认负责人**已停用** → 拦下
        async with SessionLocal() as session:
            fu3 = FollowUp(
                customer_id=mine_id,
                owner_id=stop_user_id,  # 历史跟进的负责人是个停用账号
                followup_type="电话",
                content=f"{MARKER}跟进（负责人已停用）",
            )
            session.add(fu3)
            await session.flush()
            fu3_id = fu3.id
            followup_ids.append(fu3_id)
            await session.commit()
        status, res = call(
            "POST",
            f"/followups/{fu3_id}/create-next-task",
            token=admin,
            body={
                "title": f"{MARKER}任务4",
                "due_at": (datetime.now(UTC) + timedelta(days=6)).isoformat(),
            },
        )
        check("★默认负责人已停用 → 拒绝（原实现会建出来、分给停用账号）", status, 422)
        check(
            "理由说清是「这条跟进的负责人已停用」（点明是继承来的那位，不是让你去选）",
            "这条跟进的负责人" in str(res.get("message", ""))
            and "已停用" in str(res.get("message", "")),
            True,
        )
        check(
            "被拒时没有落任务",
            await _task_count(f"{MARKER}任务4%"),
            0,
        )
        check("也没有改写历史跟进的原负责人", await _followup_owner(fu3_id), stop_user_id)

        # ③ 显式选停用员工 → 同样拦
        status, res = call(
            "POST",
            f"/followups/{fu3_id}/create-next-task",
            token=admin,
            body={
                "title": f"{MARKER}任务5",
                "due_at": (datetime.now(UTC) + timedelta(days=6)).isoformat(),
                "owner_id": stop_user_id,
            },
        )
        check("显式选停用员工 → 同样拒绝", status, 422)

        # ④ 改选合法在职 → 成功
        data = api(
            "POST",
            f"/followups/{fu3_id}/create-next-task",
            {
                "title": f"{MARKER}任务6",
                "due_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
                "owner_id": admin_id,
            },
        )
        task_ids.append(data["task_id"])
        check("改选在职负责人 → 成功且确实是这个人", (await task_row(data["task_id"]))["owner_id"], admin_id)

    finally:
        print("\n=== 收尾清理 ===")

        # 分段、各自独立事务 + 按前缀范围删（清理里任一段抛错不影响其余）
        async def _drop(label: str, stmt) -> None:
            try:
                async with SessionLocal() as session:
                    await session.execute(stmt)
                    await session.commit()
            except Exception as exc:  # noqa: BLE001
                print(f"  清理「{label}」失败（不影响其余）：{exc.__class__.__name__}")

        doomed_customers = select(Customer.id).where(Customer.name.like(f"{MARKER}%"))
        doomed_quotes = select(Quote.id).where(Quote.customer_id.in_(doomed_customers))
        await _drop("任务", delete(Task).where(Task.title.like(f"{MARKER}%")))
        await _drop("跟进", delete(FollowUp).where(FollowUp.customer_id.in_(doomed_customers)))
        await _drop("报价版本", delete(QuoteVersion).where(QuoteVersion.quote_id.in_(doomed_quotes)))
        await _drop("报价", delete(Quote).where(Quote.id.in_(doomed_quotes)))
        await _drop("报价（按单号）", delete(Quote).where(Quote.quote_no.like(f"{MARKER}%")))
        await _drop("订单", delete(SalesOrder).where(SalesOrder.order_no.like(f"{MARKER}%")))
        await _drop(
            "打样", delete(SampleRequest).where(SampleRequest.customer_id.in_(doomed_customers))
        )
        await _drop(
            "商机", delete(Opportunity).where(Opportunity.title.like(f"{MARKER}%"))
        )
        await _drop(
            "审计", delete(AuditLog).where(AuditLog.after_data.cast(String).contains(MARKER))
        )
        await _drop(
            "审计（before）",
            delete(AuditLog).where(AuditLog.before_data.cast(String).contains(MARKER)),
        )
        await _drop("客户", delete(Customer).where(Customer.name.like(f"{MARKER}%")))
        await _drop("停用员工", delete(User).where(User.name.like(f"{MARKER}%")))
        print(f"  已清（按前缀 {MARKER} 范围收）")

    if FAILURES:
        print(f"\n失败 {len(FAILURES)} 项：{FAILURES}")
        raise SystemExit(1)
    print(
        "\nOK 补建后续任务：报价/订单/打样都继承（打样记在来源业务对象上）、"
        "跨客户脏关联不带过去且明确说明、重复补建不产生第二张、"
        "负责人不分来源一律查在职"
    )


async def _task_count(prefix: str) -> int:
    async with SessionLocal() as session:
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(Task).where(Task.title.like(prefix))
                )
            ).scalar_one()
        )


async def _followup_owner(followup_id: int) -> int | None:
    async with SessionLocal() as session:
        return (
            await session.execute(select(FollowUp.owner_id).where(FollowUp.id == followup_id))
        ).scalar_one_or_none()


if __name__ == "__main__":
    asyncio.run(main())
