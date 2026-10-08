"""交接之后历史单据给不给新人看：把这条口径钉成断言。

**只在隔离库跑**：必须显式给 `DATABASE_URL`（库名以 `crm_iso` / `crm_check` 开头）
和 `API_BASE`（默认的 8000 是开发后端）。本套件会真的建客户与单据、真的改归属。

## 这条口径（2026-10-07 业务拍板）

    客户的负责人从 A 换成 B 时，**原本挂在 A 名下、属于这个客户的单据一起改成 B**。

改之前不是这样：只有**离职交接**会把单据搬过去，日常的「转移负责人／主管分配／
批量转移／撞单裁定／公海指派」**只改客户和没办完的待办**，单据原地不动 ——
于是"客户给了新人，新人打开这个客户，订单/报价标签是空的"（子资源接口按
**单据自己的负责人**过滤数据范围），而 AI 的「客户全貌」按"客户可见即资料可见"
又把它们讲了出来，两条路口径打架。

## 为什么自己造三个账号

要验的是"**新负责人是个只管自己的业务员**时，他能不能看到搬过来的历史单据"。
seed 里的张三/李四不合用：李四是销售主管（本部门都看得见，验不出单据有没有搬），
张三在交接后连客户都看不到了（那是客户级可见性）。所以三个账号都用**业务员**角色
（数据范围 self），造完自己清掉。

## 这个套件钉住这些事

1. 转移后，原负责人名下的**订单**跟着到新负责人名下
2. **商机、报价、打样**同样跟着走（打样的两个责任字段都要看）
3. 没办完的**待办**跟着走；**已完成的不动**（处理人与完成时间要留在档案里）
4. **别的在职同事负责的单子不动**（第六批审查第 4 条那条口径）
5. 订单的**业绩归属**（`sales_owner_id`）不变 —— "交接后保留历史业绩归属"
6. 单据的**历史创建人**（`created_by`）不变
7. 归属历史里多了一条变更记录
8. **新负责人**用接口只看得到搬过来的那一张（同事那张仍看不到）
9. **旧负责人**用订单明细接口已经看不到它（403）
10. **放进公海不搬**：单据留在最后经手人名下
11. **有人从公海接走时，按归属历史把单据一并接过来**
12. 接过来时业绩归属仍然不变
13. **打样的历史生成文件**（`customer_id` 为空、只挂来源打样单）跟着跟单责任走 ——
    返修点：漏了它，文件还挂在原负责人名下，接手人打开被 403
14. 只变了**生产责任**的打样单，它的文件**不**跟着走（文件跟跟单责任）；
    在职同事名下的文件也不动
15. 转移接口在 `data` 里回了「单据跟着走」的结果（页面靠它提示）
16. **并发**：客户改派的同时同事把订单接走（用行锁确定性复现）—— 订单留在同事手里，
    不被覆盖；业绩归属不变；返回里如实报出"有 1 张被同事先接走"
17. 两条交接路共用的类别名单不漂移（`DOCUMENT_KINDS` 对得上离职交接的名单，
    `DOCUMENT_KIND_LABEL` 覆盖全部类别）
18. **离职交接首跑的逐项搬运**也是"带条件的更新"：同事中途接走的业务行与生成文件
    都不被覆盖（直接测服务层那两个搬运函数，用行锁确定性复现。注意这一条只覆盖
    "搬运辅助函数"，**客户那一格不在其中** —— 见下一条）
19. **离职交接首跑真的会跳过被同事接手的客户**（第九批复审 P1）：调**完整**的
    `transfer_relations()`（不是只测辅助函数）—— 交接进行中同事把客户接走，
    客户留在同事手里、该项记「跳过：客户已由其他同事接手」、不新增归属历史、
    也不执行这次客户转移附带的待办迁移；成功数/跳过数按实际结果汇总
20. **「跳过」要分清两类**（第九批复审 P1 的收尾）：被同事先动过（客户被接手、
    各类负责人已改）与真·无需处理（对象已不存在、本次未要求转接）不能共用一句
    「无需处理」—— 前者序列化时标 `crm_taken`，界面单独列出来；判据用的原因
    登记表与 `service.py` 的真实调用点**双向对账**（改名只改一边要能查出来）
21. **被主管放回公海的客户也不接走**（第九批复审收尾，2026-10-07）：盘点到执行
    之间客户被放回公海（负责人为空）—— 交接同样跳过、让它留在公海，不覆盖主管
    刚做的调整、也不新增归属历史；原因是单独一句「客户已回到公海」，与"被别人
    接手"分开显示。⚠️ 这条只对**客户**成立：报价单/订单等没有"公海"概念，
    它们的"无主"确实只是"还没分配"，那边照旧可以接走
22. **从回收站「恢复」时指定新负责人**（回收站复审第三轮，2026-10-08）也要办
    同一套事 —— 从前那条路只把客户负责人一改了事：新人打开原报价/原订单是 403、
    客户下的待办是空列表，单据负责人还写着旧人，公海标记也不动。
    现在归属历史、未完成待办、名下单据、公海/私海标记一起办完（与「转移负责人」
    共用同一个服务函数），**只搬原负责人名下的**，其他在职同事的活与业绩归属不动；
    反过来，**不换人**的恢复只撤销删除标记，一个字段都不动、也不写归属历史

跑法：

    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 \\
      DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_iso_test \\
      PYTHONPATH=. .venv/bin/python scripts/check_customer_handover_documents.py
"""

import asyncio
import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

import app.main  # noqa: F401  保证所有模型都注册进 metadata

from sqlalchemy import func, select, text

from app.core.database import SessionLocal, engine
from app.core.security import hash_password
from app.modules.customer.documents import DOCUMENT_KINDS
from app.modules.user.model import User, user_roles
from app.modules.wecom.model import TRANSFER_KIND_LABEL

FAILURES: list[str] = []
PREFIX = "CHKHANDOVER"
STAMP = str(int(time.time()))
BASE = os.environ.get("API_BASE", "")
PASSWORD = "CHKhandover123"


def check(label: str, actual, expected) -> None:
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: str = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}' + (f"：{detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def require_isolated_db() -> str:
    """显式要求一次性隔离库：本套件会真的建客户、建账号、改归属。"""
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise SystemExit(
            "必须显式设置 DATABASE_URL（一次性隔离库，库名以 crm_iso / crm_check 开头）"
        )
    name = url.rsplit("/", 1)[-1].split("?")[0]
    if not name.startswith(("crm_iso", "crm_check")):
        raise SystemExit(f"拒绝执行：DATABASE_URL 指向 {name!r}，不是一次性隔离库")
    if not BASE:
        raise SystemExit(
            "必须显式设置 API_BASE（默认的 8000 是开发后端）。"
            "例：API_BASE=http://127.0.0.1:8001/api/v1"
        )
    return name


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


def items_of(res: dict) -> list[dict]:
    """列表接口的 items；出错时 data 可能是 None，别让解析把套件崩掉。"""
    return (res.get("data") or {}).get("items") or []


async def cleanup() -> None:
    """自底向上清干净。

    本套件用**直接写库**造夹具（跟着 `check_wecom_handover` 的做法：字段可控、
    不依赖界面流程），所以依赖链很短；但**顺序不能乱**：业务行都引用 users，
    所以用户必须最后删；报价那三张表也要先删，别让外键把清理打断
    （清理一中断，夹具就留在库里，最后被守门套件抓出来）。
    """
    async with SessionLocal() as s:
        cust = "(select id from customers where name like :p)"
        quote_ids = f"(select id from quotes where customer_id in {cust})"
        version_ids = f"(select id from quote_versions where quote_id in {quote_ids})"
        for sql in (
            f"delete from quote_items where quote_version_id in {version_ids}",
            f"delete from quote_charges where quote_version_id in {version_ids}",
            f"delete from quote_versions where quote_id in {quote_ids}",
            f"delete from quotes where customer_id in {cust}",
            f"delete from sample_requests where customer_id in {cust}",
            f"delete from sales_orders where customer_id in {cust}",
            f"delete from opportunities where customer_id in {cust}",
            f"delete from tasks where customer_id in {cust}",
            f"delete from order_drafts where customer_id in {cust}",
            f"delete from customer_owner_history where customer_id in {cust}",
            # 离职交接的首跑记录（第 9 段会真的调 `transfer_relations`）。
            # **必须在删 users 之前**：`wecom_sync_jobs.operator_id` 引用 users，
            # 顺序反了清理就中断、夹具留在库里被守门套件抓出来。
            "delete from wecom_transfer_items where job_id in "
            "(select id from wecom_sync_jobs where detail::text like :m)",
            "delete from wecom_sync_jobs where detail::text like :m",
            "delete from customers where name like :p",
            # 生成的对客文件（本套件造的历史件，owner 都挂在三个夹具账号名下）。
            # **必须在删 users 之前**：biz_docs 引用用户，顺序反了清理就中断，
            # 夹具留在库里会被守门套件抓出来。
            "delete from biz_docs where owner_id in "
            "(select id from users where username like :u)",
            "delete from user_roles where user_id in "
            "(select id from users where username like :u)",
            "delete from users where username like :u",
        ):
            await s.execute(
                text(sql), {"p": f"{PREFIX}%", "m": f"%{PREFIX}%", "u": f"{PREFIX.lower()}_%"}
            )
        await s.commit()
    # 显式收池：async 引擎的连接池绑在创建它的那个事件循环上，
    # 留着不关容易在别的脚本里报 "attached to a different loop"。
    await engine.dispose()


async def read_state(ids: dict) -> dict:
    """把要断言的行重新读一遍（避免拿着会话里的旧对象）。"""
    from app.modules.customer.model import Customer, CustomerOwnerHistory
    from app.modules.bizdoc.model import BizDoc
    from app.modules.opportunity.model import Opportunity
    from app.modules.order.model import SalesOrder
    from app.modules.quote.model import Quote
    from app.modules.sample.model import SampleRequest
    from app.modules.task.model import Task

    async with SessionLocal() as s:
        order_mine = await s.get(SalesOrder, ids["order_mine"])
        order_colleague = await s.get(SalesOrder, ids["order_colleague"])
        sample = await s.get(SampleRequest, ids["sample"])
        return {
            "customer_owner": (await s.get(Customer, ids["customer"])).owner_id,
            "opportunity_owner": (await s.get(Opportunity, ids["opportunity"])).owner_id,
            "quote_owner": (await s.get(Quote, ids["quote"])).owner_id,
            "order_mine_owner": order_mine.owner_id,
            "order_mine_sales_owner": order_mine.sales_owner_id,
            "order_mine_created_by": order_mine.created_by,
            "order_colleague_owner": order_colleague.owner_id,
            "sample_owner": sample.owner_id,
            "sample_production_owner": sample.production_owner_id,
            "open_task_owner": (await s.get(Task, ids["open_task"])).owner_id,
            "done_task_owner": (await s.get(Task, ids["done_task"])).owner_id,
            # 历史生成文件（customer_id 为空的那三张）
            "doc_mine_owner": (await s.get(BizDoc, ids["doc_mine"])).owner_id,
            "doc_prod_only_owner": (await s.get(BizDoc, ids["doc_prod_only"])).owner_id,
            "doc_colleague_owner": (await s.get(BizDoc, ids["doc_colleague"])).owner_id,
            "history_count": (
                await s.execute(
                    select(func.count()).select_from(CustomerOwnerHistory).where(
                        CustomerOwnerHistory.customer_id == ids["customer"]
                    )
                )
            ).scalar_one(),
        }


async def _sample_doc_template_id(session) -> int:
    """取打样需求单的模板 id（没有就先播种）。

    `biz_docs.template_id` 是**非空外键**，而默认模板由
    `bizdoc.service.ensure_default_templates` 幂等播种 —— `seed.py` 不管它，
    全新库里这张表是空的（踩过：直接查会 `scalar_one()` 报无行）。
    """
    from app.modules.bizdoc import service as bizdoc_service
    from app.modules.bizdoc.model import BizDocTemplate

    await bizdoc_service.ensure_default_templates(session)
    return (
        await session.execute(
            select(BizDocTemplate.id)
            .where(BizDocTemplate.doc_type == "sample_request")
            .order_by(BizDocTemplate.version.desc())
            .limit(1)
        )
    ).scalar_one()


async def _make_doc(session, template_id: int, doc_no: str, *, owner_id: int, sample_id: int) -> int:
    """造一张**历史**生成文件：`customer_id` 为空、只挂来源打样单。

    这张表要求 `doc_no` 唯一、`input_snapshot` / `content_sha256` 非空，
    `template_id` 还是非空外键 —— 所以模板必须从库里现取一张。
    """
    from app.modules.bizdoc.model import BizDoc

    doc = BizDoc(
        doc_no=doc_no,
        doc_type="sample_request",
        title=f"{PREFIX}历史打样文件",
        version=1,
        status="active",
        owner_id=owner_id,
        customer_id=None,  # ← 关键：历史行没有客户编号，只能靠来源单据追
        sample_request_id=sample_id,
        template_id=template_id,
        template_version=1,
        input_snapshot={},
        content_sha256="a" * 64,
        created_at=datetime.now(UTC),
    )
    session.add(doc)
    await session.flush()
    return doc.id


async def seed_fixtures() -> dict:
    """一个客户 + 各类单据 + 三个**业务员**账号（原负责人／新负责人／在职同事）。"""
    from app.modules.customer.model import Customer
    from app.modules.opportunity.model import Opportunity
    from app.modules.order.model import SalesOrder
    from app.modules.quote.model import Quote
    from app.modules.sample.model import SampleRequest
    from app.modules.task.model import Task

    ids: dict = {}
    async with SessionLocal() as s:
        admin = (
            await s.execute(select(User).where(User.username == "admin"))
        ).scalars().first()
        if admin is None:
            raise SystemExit("库里没有 admin 账号，先跑 scripts/seed.py")
        ids["admin"] = admin.id
        dept_id = (
            await s.execute(text("select id from departments order by id limit 1"))
        ).scalar_one_or_none()
        role_id = (
            await s.execute(text("select id from roles where code = 'salesperson'"))
        ).scalar_one()
        stage_id = (
            await s.execute(text("select id from opportunity_stages order by id limit 1"))
        ).scalar_one()

        def make_user(tag: str, label: str) -> User:
            return User(
                username=f"{PREFIX.lower()}_{tag}_{STAMP}", name=f"{PREFIX}{label}-{STAMP}",
                password_hash=hash_password(PASSWORD), status="active",
                department_id=dept_id,
            )

        from_owner = make_user("from", "原负责人")
        to_owner = make_user("to", "新负责人")
        colleague = make_user("col", "在职同事")
        s.add_all([from_owner, to_owner, colleague])
        await s.flush()
        for user in (from_owner, to_owner, colleague):
            await s.execute(user_roles.insert().values(user_id=user.id, role_id=role_id))
        ids["from"], ids["to"], ids["colleague"] = (
            from_owner.id, to_owner.id, colleague.id,
        )
        ids["from_username"], ids["to_username"] = from_owner.username, to_owner.username

        customer = Customer(
            name=f"{PREFIX}交接客户-{STAMP}", owner_id=from_owner.id,
            status="active", pool_status="private", level="A",
        )
        s.add(customer)
        await s.flush()
        ids["customer"] = customer.id

        # 订单的"业绩归属"与"历史创建人"刻意写成**第三个人**：
        # 这样"搬负责人时不许动这两个字段"才验得出来。
        order_mine = SalesOrder(
            order_no=f"{PREFIX}O1{STAMP}", customer_id=customer.id, total_amount=1000,
            currency="CNY", status="pending", owner_id=from_owner.id,
            sales_owner_id=colleague.id, created_by=colleague.id,
        )
        # 在职同事负责的订单：客户交接时**不该**被搬走
        order_colleague = SalesOrder(
            order_no=f"{PREFIX}O2{STAMP}", customer_id=customer.id, total_amount=2000,
            currency="CNY", status="pending", owner_id=colleague.id,
            sales_owner_id=colleague.id, created_by=colleague.id,
        )
        # 打样单两份，形状刻意不同：
        #   - `sample_mine`：跟单+生产**都是原负责人** → 它的历史生成文件应当跟着走；
        #   - `sample_prod_only`：只有**生产责任**是原负责人（跟单是在职同事）
        #     → 文件跟着**跟单责任**走，所以不该被搬。
        sample_mine = SampleRequest(
            customer_id=customer.id, owner_id=from_owner.id,
            production_owner_id=from_owner.id, status="approved",
            requested_at=datetime.now(UTC),
        )
        sample_prod_only = SampleRequest(
            customer_id=customer.id, owner_id=colleague.id,
            production_owner_id=from_owner.id, status="approved",
            requested_at=datetime.now(UTC),
        )
        s.add_all([
            Opportunity(
                customer_id=customer.id, title=f"{PREFIX}商机-{STAMP}", stage_id=stage_id,
                currency="CNY", status="active", owner_id=from_owner.id,
            ),
            Quote(
                quote_no=f"{PREFIX}Q{STAMP}", customer_id=customer.id,
                owner_id=from_owner.id, status="draft", created_by=from_owner.id,
            ),
            sample_mine,
            sample_prod_only,
            Task(
                customer_id=customer.id, title=f"{PREFIX}没办完的待办-{STAMP}",
                owner_id=from_owner.id, priority="normal", status="pending", source="manual",
            ),
            Task(
                customer_id=customer.id, title=f"{PREFIX}已完成的待办-{STAMP}",
                owner_id=from_owner.id, priority="normal", status="done", source="manual",
            ),
            order_mine,
            order_colleague,
        ])
        await s.flush()
        ids["order_mine"], ids["order_colleague"] = order_mine.id, order_colleague.id
        ids["opportunity"] = (
            await s.execute(
                select(Opportunity.id).where(Opportunity.customer_id == customer.id)
            )
        ).scalar_one()
        ids["quote"] = (
            await s.execute(select(Quote.id).where(Quote.customer_id == customer.id))
        ).scalar_one()
        ids["sample"] = sample_mine.id
        ids["sample_prod_only"] = sample_prod_only.id

        # 三张**历史**生成文件：`customer_id` 为空、只挂了 `sample_request_id` ——
        # 正是复验里"打样文件漏迁、接手人打开被 403"的那种形态。
        # 第三张刻意挂在**在职同事**名下：它不该被搬（只搬原负责人名下的）。
        template_id = await _sample_doc_template_id(s)
        ids["doc_mine"], ids["doc_prod_only"], ids["doc_colleague"] = (
            await _make_doc(s, template_id, f"{PREFIX}DOC-MINE-{STAMP}",
                            owner_id=from_owner.id, sample_id=sample_mine.id),
            await _make_doc(s, template_id, f"{PREFIX}DOC-PROD-{STAMP}",
                            owner_id=from_owner.id, sample_id=sample_prod_only.id),
            await _make_doc(s, template_id, f"{PREFIX}DOC-COL-{STAMP}",
                            owner_id=colleague.id, sample_id=sample_mine.id),
        )
        task_rows = (
            await s.execute(
                select(Task.id, Task.status).where(Task.customer_id == customer.id)
            )
        ).all()
        ids["open_task"] = next(r[0] for r in task_rows if r[1] == "pending")
        ids["done_task"] = next(r[0] for r in task_rows if r[1] == "done")
        # 必须提交：接口那边是**另一个会话**，只 flush 的话会话一关就全回滚，
        # 接口那边只会回 404（这个坑第一版就踩了）。
        await s.commit()
    return ids


async def _wait_until_locked(table: str, timeout: float = 20.0) -> bool:
    """轮询数据库，等一个"正在等某张表行锁"的后端出现。

    这是**确定性复现并发**的关键一步：只有确认被验的那个调用**已经卡住**了，
    才去提交那个未提交的事务；否则就成了"赌时序" —— 本机可能绿、CI 上偶发红，
    还查不出原因。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        async with SessionLocal() as probe:
            waiting = (
                await probe.execute(
                    text(
                        "select count(*) from pg_stat_activity "
                        "where datname = current_database() "
                        "and wait_event_type = 'Lock' "
                        "and query ilike :t"
                    ),
                    {"t": f"%{table}%"},
                )
            ).scalar_one()
        if waiting:
            return True
        await asyncio.sleep(0.1)
    return False


async def assert_concurrent_takeover(ids: dict, admin_token: str) -> None:
    """并发：客户改派的同时，同事把订单接走 —— 订单必须留在同事手里。

    **复现步骤（就是复验里的那段）**：
      ① 一个事务把订单改派给同事 C（**未提交**，握着那一行的锁）；
      ② 同时把客户从 A 转给 B —— 转移会卡在同一个行锁上；
      ③ C 提交；转移继续执行。
    修好之前，转移"先按条件查 id、再按 id 更新"，拿到锁之后照样命中，把 C 的改派
    吃掉了；修好之后，带条件的更新发现"负责人已经不是 A 了"，跳过这一行，
    并在返回里报"有 1 张单据被同事先接走"。

    单独用一个客户做这件事，免得上面对共享客户的断言被这里改花。
    """
    from app.modules.customer.model import Customer
    from app.modules.order.model import SalesOrder

    async with SessionLocal() as s:
        cust = Customer(
            name=f"{PREFIX}并发客户-{STAMP}", owner_id=ids["from"],
            status="active", pool_status="private", level="A",
        )
        s.add(cust)
        await s.flush()
        order = SalesOrder(
            order_no=f"{PREFIX}OC{STAMP}", customer_id=cust.id, total_amount=500,
            currency="CNY", status="pending", owner_id=ids["from"],
            # 业绩归属与历史创建人刻意写成第三个人：并发下这两个字段也不许动
            sales_owner_id=ids["colleague"], created_by=ids["colleague"],
        )
        s.add(order)
        await s.flush()
        cid, order_id = cust.id, order.id
        await s.commit()

    answer: dict = {}

    def worker() -> None:
        answer["res"] = call(
            "POST", f"/customers/{cid}/transfer", admin_token,
            {"owner_id": ids["to"], "reason": "CHK 并发：改派与同事接手同时发生"},
        )

    async with SessionLocal() as holder:
        # ① 未提交：同事把这笔订单接走（握着该行的写锁）
        await holder.execute(
            text("update sales_orders set owner_id = :c where id = :o"),
            {"c": ids["colleague"], "o": order_id},
        )
        # ② 发起客户转移；它会卡在这笔订单的行锁上
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        blocked = await _wait_until_locked("sales_orders")
        # ③ 放行
        await holder.commit()
        await asyncio.to_thread(thread.join, 30)

    status, res = answer.get("res", (0, {}))
    check_true("并发复现有效：转移确实卡在了同事那笔订单的行锁上", blocked,
               f"观测到「等行锁的后端」={blocked}")
    check_true("并发下转移本身成功", status == 200 and res.get("code") == 0,
               f"HTTP {status} {res.get('message')}")

    async with SessionLocal() as s:
        row = await s.get(SalesOrder, order_id)
        owner, sales_owner = row.owner_id, row.sales_owner_id
    check("并发下：同事刚接手的订单**没有被覆盖**，仍归同事", owner, ids["colleague"])
    check("并发下：业绩归属一个字没动", sales_owner, ids["colleague"])

    doc = (res.get("data") or {}).get("document_transfer") or {}
    check_true("转移结果如实交代「有单据被同事先接走」",
               (doc.get("skipped_total") or 0) >= 1, str(doc))
    check_true("跳过的类别里点到了销售订单",
               "销售订单" in (doc.get("skipped_labels") or []), str(doc))

    # 收尾：这个夹具客户自己删掉（清理函数按客户名前缀也会兜底）
    async with SessionLocal() as s:
        for sql in (
            "delete from sales_orders where customer_id = :c",
            "delete from customer_owner_history where customer_id = :c",
            "delete from customers where id = :c",
        ):
            await s.execute(text(sql), {"c": cid})
        await s.commit()


async def assert_handover_move_is_conditional(ids: dict) -> None:
    """离职交接的首跑搬运同样是"带条件的更新"：同事中途接走的不覆盖。

    这条**直接测服务层那两个搬运函数**（`_move_owner_if_still` /
    `_reassign_generated_docs`），不绕一整条企微交接流程 —— 要验的是
    "写库那一刻有没有再判一次负责人"，用行锁就能确定性复现。

    （重试那条路本来就有行锁，不用在这里验；首跑那条路原来是"先读一眼再赋值"。）
    """
    from app.modules.bizdoc.model import BizDoc
    from app.modules.sample.model import SampleRequest
    from app.modules.wecom import service as wecom_service

    async with SessionLocal() as s:
        template_id = await _sample_doc_template_id(s)
        sample = SampleRequest(
            customer_id=ids["customer"], owner_id=ids["from"],
            production_owner_id=ids["from"], status="approved",
            requested_at=datetime.now(UTC),
        )
        s.add(sample)
        await s.flush()
        sample_id = sample.id
        doc_id = await _make_doc(
            s, template_id, f"{PREFIX}DOC-CONC-{STAMP}",
            owner_id=ids["from"], sample_id=sample_id,
        )
        await s.commit()

    # ---- ① 业务行（打样单）的负责人搬运 ----
    async def move_sample() -> bool:
        async with SessionLocal() as s:
            ok = await wecom_service._move_owner_if_still(
                s, SampleRequest,
                business_id=sample_id, field="owner_id",
                from_owner_id=ids["from"], to_owner_id=ids["to"],
            )
            await s.commit()
            return ok

    async with SessionLocal() as holder:
        await holder.execute(
            text("update sample_requests set owner_id = :c where id = :i"),
            {"c": ids["colleague"], "i": sample_id},
        )
        task = asyncio.create_task(move_sample())
        blocked = await _wait_until_locked("sample_requests")
        await holder.commit()
        moved = await asyncio.wait_for(task, timeout=30)

    check_true("交接搬运并发复现有效：确实卡在了同事握着的行锁上", blocked,
               f"观测到「等行锁的后端」={blocked}")
    check_true("交接搬运：同事已接手的业务行**没有被覆盖**（函数返回未改）",
               not moved, f"_move_owner_if_still 返回 {moved}")
    async with SessionLocal() as s:
        check("交接搬运：打样单仍归同事",
              (await s.get(SampleRequest, sample_id)).owner_id, ids["colleague"])

    # ---- ② 生成文件的搬运 ----
    async def move_docs() -> int:
        async with SessionLocal() as s:
            count = await wecom_service._reassign_generated_docs(
                s, handover_id=ids["from"], to_owner_id=ids["to"],
                field="sample_request_id", business_id=sample_id,
            )
            await s.commit()
            return count

    async with SessionLocal() as holder:
        await holder.execute(
            text("update biz_docs set owner_id = :c where id = :i"),
            {"c": ids["colleague"], "i": doc_id},
        )
        task = asyncio.create_task(move_docs())
        blocked = await _wait_until_locked("biz_docs")
        await holder.commit()
        count = await asyncio.wait_for(task, timeout=30)

    check_true("交接搬运并发复现有效：文件那一侧也卡住了", blocked,
               f"观测到「等行锁的后端」={blocked}")
    check("交接搬运：同事已接手的文件没有被覆盖（实际改动 0 张）", count, 0)
    async with SessionLocal() as s:
        check("交接搬运：那张文件仍归同事",
              (await s.get(BizDoc, doc_id)).owner_id, ids["colleague"])

    # 收尾：这个夹具自己删掉
    async with SessionLocal() as s:
        await s.execute(text("delete from biz_docs where id = :d"), {"d": doc_id})
        await s.execute(text("delete from sample_requests where id = :i"), {"i": sample_id})
        await s.commit()


class _NoWeComCalls:
    """这段夹具没有企微关系，所以**不该**发出任何企微转接。

    真被调到就说明夹具造错了（多挂了企微关系），直接抛错比静默记账好 ——
    本项目 `.env` 里有真实凭据，任何"以为不会被调、其实被调了"的口子都危险。
    """

    async def transfer_customer(self, **kwargs):  # pragma: no cover - 只做保险
        raise AssertionError(f"这段交接不该发出企微转接：{kwargs}")


async def assert_handover_first_run_skips_taken_customer() -> None:
    """离职交接**首跑**：交接跑到一半，同事把客户接走了 —— 必须跳过，不许抢回来。

    这一条**调完整的 `transfer_relations()`**（不是只测搬运辅助函数）。
    复验里露出来的缺口恰恰是"辅助函数都带上条件了，客户那一格没有"：
    只测辅助函数根本照不出来，必须走完整条流程。

    **复现时序**（与复验里那段一致，用行锁做到确定性，不赌时序）：
      ① 一个**未提交**的事务把客户改派给同事 C（握着客户那一行的写锁）；
      ② 启动完整交接 —— 盘点时读到的还是旧负责人 A（C 还没提交），
         执行到"改客户负责人"时会卡在客户行锁上；
      ③ 确认交接**确实卡住**了，才让 C 提交；
      ④ 交接继续执行：必须发现"这个客户现在不是离职人的了"，记成跳过。

    断言（每一条在修好之前都会红）：
      - 客户仍归同事 C（没有被交接覆盖成接管人 B）；
      - 该项的 CRM 状态是 `skipped`、原因写明"已由其他同事接手"；
      - **没有**新增一条客户归属历史（不该留下错误记录）；
      - 成功数里**不含**这个客户，`customers_skipped` 记 1；
      - 该客户名下、离职人的待办**照它自己那一格**走（指定给了第三个人）——
        如果"客户转移附带的待办迁移"偷偷跑了，它会先被改成客户接管人，
        于是第 5 步的带条件更新就会跳掉它，这条断言随即变红。
    """
    from app.core.deps import CurrentUser
    from app.modules.customer.model import Customer, CustomerOwnerHistory
    from app.modules.task.model import Task
    from app.modules.user.model import User, user_roles
    from app.modules.wecom.model import WeComSyncJob, WeComTransferItem

    stamp = f"{STAMP}b"
    async with SessionLocal() as s:
        admin = (
            await s.execute(select(User).where(User.username == "admin"))
        ).scalars().first()
        dept_id = (
            await s.execute(text("select id from departments order by id limit 1"))
        ).scalar_one_or_none()
        role_id = (
            await s.execute(text("select id from roles where code = 'salesperson'"))
        ).scalar_one()

        def make_user(tag: str, label: str) -> User:
            return User(
                username=f"{PREFIX.lower()}_{tag}_{stamp}", name=f"{PREFIX}{label}-{stamp}",
                password_hash=hash_password(PASSWORD), status="active",
                department_id=dept_id,
            )

        # 这一段的三个账号**单独造**（不复用上面那三个）：完整交领会把"离职人"
        # 名下的东西**全量**扫一遍，共用账号会把前面几段的夹具一起搬走。
        leaver = make_user("ho2", "首跑离职人")
        taker = make_user("to2", "首跑接管人")
        colleague = make_user("col2", "首跑同事")
        s.add_all([leaver, taker, colleague])
        await s.flush()
        for user in (leaver, taker, colleague):
            await s.execute(user_roles.insert().values(user_id=user.id, role_id=role_id))

        customer = Customer(
            name=f"{PREFIX}首跑客户-{stamp}", owner_id=leaver.id,
            status="active", pool_status="private", level="A",
        )
        s.add(customer)
        await s.flush()
        task = Task(
            customer_id=customer.id, title=f"{PREFIX}首跑待办-{stamp}",
            owner_id=leaver.id, priority="normal", status="pending", source="manual",
        )
        s.add(task)
        await s.flush()
        ids = {
            "admin": admin.id, "leaver": leaver.id, "taker": taker.id,
            "colleague": colleague.id, "customer": customer.id, "task": task.id,
        }
        # 必须提交：接口/另一个会话才看得到（只 flush 会话一关就回滚）。
        await s.commit()

    async def run_full_transfer() -> int:
        """在**本进程内**跑一整条离职交接，返回任务 id。"""
        from app.modules.wecom import client as wecom_client
        from app.modules.wecom import service as wecom_service

        original = wecom_client._client
        wecom_client._client = _NoWeComCalls()
        try:
            async with SessionLocal() as s:
                admin_user = await s.get(User, ids["admin"])
                user = CurrentUser(admin_user, {"wecom:manage"}, ["admin"], "all")
                job = await wecom_service.transfer_relations(
                    s,
                    user=user,
                    handover_user_id=ids["leaver"],
                    takeover_user_id=ids["taker"],
                    # 这份夹具没有企微关系，整条企微侧无事可做
                    transfer_wecom=False,
                    # 待办指定给**第三个人**：见函数说明里最后那条断言
                    item_assignees={f"task:{ids['task']}": ids["colleague"]},
                )
                job_id = job.id
                await s.commit()
            return job_id
        finally:
            wecom_client._client = original

    async with SessionLocal() as holder:
        # ① 未提交：同事把这个客户接走（握着客户那一行的写锁）
        await holder.execute(
            text("update customers set owner_id = :c where id = :i"),
            {"c": ids["colleague"], "i": ids["customer"]},
        )
        # ② 启动完整交接；它会卡在客户行锁上
        running = asyncio.create_task(run_full_transfer())
        blocked = await _wait_until_locked("customers")
        # ③ 确认卡住之后再放行
        await holder.commit()
        job_id = await asyncio.wait_for(running, timeout=60)

    check_true("并发复现有效：交接确实卡在了同事握着的客户行锁上", blocked,
               f"观测到「等行锁的后端」={blocked}")

    async with SessionLocal() as s:
        owner = (await s.get(Customer, ids["customer"])).owner_id
        history = (
            await s.execute(
                select(func.count()).select_from(CustomerOwnerHistory).where(
                    CustomerOwnerHistory.customer_id == ids["customer"]
                )
            )
        ).scalar_one()
        customer_item = (
            await s.execute(
                select(WeComTransferItem).where(
                    WeComTransferItem.job_id == job_id,
                    WeComTransferItem.kind == "customer",
                    WeComTransferItem.business_id == ids["customer"],
                )
            )
        ).scalars().one()
        task_item = (
            await s.execute(
                select(WeComTransferItem).where(
                    WeComTransferItem.job_id == job_id,
                    WeComTransferItem.kind == "task",
                    WeComTransferItem.business_id == ids["task"],
                )
            )
        ).scalars().one()
        task_owner = (await s.get(Task, ids["task"])).owner_id
        job = await s.get(WeComSyncJob, job_id)
        detail = dict(job.detail or {})
        success_count = job.success_count
        customer_status, customer_error = customer_item.crm_status, customer_item.crm_error
        task_status = task_item.crm_status
        # 界面拿到的就是这一份（`serialize_transfer_item`）—— 判定要在下发的形态上做
        from app.modules.wecom.service import serialize_transfer_item

        customer_payload = serialize_transfer_item(customer_item)

    check("并发下：客户仍在同事手里（没有被交接抢走）", owner, ids["colleague"])
    check("交接项记成「跳过」而不是「已交接」", customer_status, "skipped")
    check_true("跳过原因说清了是「已由其他同事接手」",
               "其他同事接手" in (customer_error or ""), customer_error or "")
    check("跳过时**没有**新增客户归属历史（不留错误记录）", history, 0)
    check("客户分类统计按实际结果：成功 0 个、跳过 1 个",
          (detail.get("customers"), detail.get("customers_skipped")), (0, 1))
    check("跳过时**没有**执行这次客户转移附带的待办迁移", task_owner, ids["colleague"])
    check("待办那一项照它自己那一格交接成功（走的是第 5 段，不是客户那一格）",
          task_status, "moved")
    check("成功数只算真正改掉的项（被跳过的那一项不计进去）", success_count, 1)
    check_true("该跳过项被单独标成「已被他人先动过」（crm_taken），不再混进「无需处理」",
               customer_payload.get("crm_taken") is True,
               f"crm_taken={customer_payload.get('crm_taken')!r}")

    # 收尾：这段夹具自己删掉（全局 cleanup 也会按前缀兜底）
    async with SessionLocal() as s:
        await s.execute(
            text("delete from wecom_transfer_items where job_id = :j"), {"j": job_id}
        )
        await s.execute(text("delete from wecom_sync_jobs where id = :j"), {"j": job_id})
        await s.execute(text("delete from customer_owner_history where customer_id = :c"),
                        {"c": ids["customer"]})
        await s.execute(text("delete from tasks where id = :t"), {"t": ids["task"]})
        await s.execute(text("delete from customers where id = :c"), {"c": ids["customer"]})
        await s.execute(text("delete from user_roles where user_id in (:a, :b, :d)"),
                        {"a": ids["leaver"], "b": ids["taker"], "d": ids["colleague"]})
        await s.execute(text("delete from users where id in (:a, :b, :d)"),
                        {"a": ids["leaver"], "b": ids["taker"], "d": ids["colleague"]})
        await s.commit()


async def assert_handover_first_run_skips_pooled_customer() -> None:
    """离职交接**首跑**：交接跑到一半，主管把客户放回了公海 —— 必须跳过，留在公海。

    第九批复审收尾（2026-10-07）。原来的判据写着"当前没有负责人不算被别人接手，
    那是没人管、接过去不侵害谁"，于是**主管在盘点与执行之间主动把客户放回公海**的
    这种情况会被交接重新接走 —— 覆盖掉一次刚发生、且已经留痕的归属调整，
    还多写一条"从公海被接走"的历史把这件事盖住。

    现在口径改为：只要客户**已经不归离职人**（含被放回公海）就跳过 ——
    跳过后客户留在公海，主管想给谁重新指派即可。

    **复现时序**（用行锁做到确定性，不赌时序）：
      ① 一个**未提交**的事务把客户放回公海（owner_id 置空、pool_status=public），
         握着客户那一行的写锁；
      ② 启动完整交接 —— 盘点时读到的还是旧负责人，执行到时卡在客户行锁上；
      ③ 确认交接**确实卡住**了，才提交；
      ④ 交接继续执行：必须发现"这个客户已经不归离职人了"，记成跳过。

    断言（每一条在修好之前都会红）：
      - 客户**留在公海**（负责人仍为空），没有被交接接走；
      - 该项的 CRM 状态是 `skipped`、原因写明"已回到公海"；
      - **没有**新增一条客户归属历史（不留错误记录）；
      - 成功数里**不含**这个客户，`customers_skipped` 记 1；
      - 该跳过项被单独标成 `crm_taken`，不会混进「无需处理」。
    """
    from app.core.deps import CurrentUser
    from app.modules.customer.model import Customer, CustomerOwnerHistory
    from app.modules.user.model import User, user_roles
    from app.modules.wecom.model import WeComSyncJob, WeComTransferItem

    stamp = f"{STAMP}c"
    async with SessionLocal() as s:
        admin = (
            await s.execute(select(User).where(User.username == "admin"))
        ).scalars().first()
        dept_id = (
            await s.execute(text("select id from departments order by id limit 1"))
        ).scalar_one_or_none()
        role_id = (
            await s.execute(text("select id from roles where code = 'salesperson'"))
        ).scalar_one()

        def make_user(tag: str, label: str) -> User:
            return User(
                username=f"{PREFIX.lower()}_{tag}_{stamp}", name=f"{PREFIX}{label}-{stamp}",
                password_hash=hash_password(PASSWORD), status="active",
                department_id=dept_id,
            )

        # 这一段单独造账号：完整交领会把离职人名下的东西**全量**扫一遍，
        # 共用账号会把前面几段的夹具一起搬走。
        leaver = make_user("ho3", "公海离职人")
        taker = make_user("to3", "公海接管人")
        s.add_all([leaver, taker])
        await s.flush()
        for user in (leaver, taker):
            await s.execute(user_roles.insert().values(user_id=user.id, role_id=role_id))

        customer = Customer(
            name=f"{PREFIX}公海客户-{stamp}", owner_id=leaver.id,
            status="active", pool_status="private", level="A",
        )
        s.add(customer)
        await s.flush()
        ids = {
            "admin": admin.id, "leaver": leaver.id, "taker": taker.id,
            "customer": customer.id,
        }
        # 必须提交：接口/另一个会话才看得到（只 flush 会话一关就回滚）。
        await s.commit()

    async def run_full_transfer() -> int:
        """在**本进程内**跑一整条离职交接，返回任务 id。"""
        from app.modules.wecom import client as wecom_client
        from app.modules.wecom import service as wecom_service

        original = wecom_client._client
        wecom_client._client = _NoWeComCalls()
        try:
            async with SessionLocal() as s:
                admin_user = await s.get(User, ids["admin"])
                user = CurrentUser(admin_user, {"wecom:manage"}, ["admin"], "all")
                job = await wecom_service.transfer_relations(
                    s,
                    user=user,
                    handover_user_id=ids["leaver"],
                    takeover_user_id=ids["taker"],
                    # 这份夹具没有企微关系，整条企微侧无事可做
                    transfer_wecom=False,
                )
                job_id = job.id
                await s.commit()
            return job_id
        finally:
            wecom_client._client = original

    async with SessionLocal() as holder:
        # ① 未提交：主管把客户放回公海（握着客户那一行的写锁）
        await holder.execute(
            text("update customers set owner_id = null, pool_status = 'public' where id = :i"),
            {"i": ids["customer"]},
        )
        # ② 启动完整交接；它会卡在客户行锁上
        running = asyncio.create_task(run_full_transfer())
        blocked = await _wait_until_locked("customers")
        # ③ 确认卡住之后再放行
        await holder.commit()
        job_id = await asyncio.wait_for(running, timeout=60)

    check_true("并发复现有效：交接确实卡在了被放回公海的那行客户上", blocked,
               f"观测到「等行锁的后端」={blocked}")

    async with SessionLocal() as s:
        owner = (await s.get(Customer, ids["customer"])).owner_id
        history = (
            await s.execute(
                select(func.count()).select_from(CustomerOwnerHistory).where(
                    CustomerOwnerHistory.customer_id == ids["customer"]
                )
            )
        ).scalar_one()
        customer_item = (
            await s.execute(
                select(WeComTransferItem).where(
                    WeComTransferItem.job_id == job_id,
                    WeComTransferItem.kind == "customer",
                    WeComTransferItem.business_id == ids["customer"],
                )
            )
        ).scalars().one()
        job = await s.get(WeComSyncJob, job_id)
        detail = dict(job.detail or {})
        success_count = job.success_count
        customer_status, customer_error = customer_item.crm_status, customer_item.crm_error
        # 界面拿到的就是这一份（`serialize_transfer_item`）—— 判定要在下发的形态上做
        from app.modules.wecom.service import serialize_transfer_item

        customer_payload = serialize_transfer_item(customer_item)

    check("客户**留在了公海**（没有被交接接走）", owner, None)
    check("交接项记成「跳过」而不是「已交接」", customer_status, "skipped")
    check_true("跳过原因说清了是「已回到公海」",
               "回到公海" in (customer_error or ""), customer_error or "")
    check("跳过时**没有**新增客户归属历史（不留错误记录）", history, 0)
    check("客户分类统计按实际结果：成功 0 个、跳过 1 个",
          (detail.get("customers"), detail.get("customers_skipped")), (0, 1))
    check("成功数只算真正改掉的项（被跳过的那一项不计进去）", success_count, 0)
    check_true("该跳过项被单独标成「已被他人先动过」（crm_taken），不再混进「无需处理」",
               customer_payload.get("crm_taken") is True,
               f"crm_taken={customer_payload.get('crm_taken')!r}")

    # 收尾：这段夹具自己删掉（全局 cleanup 也会按前缀兜底）
    async with SessionLocal() as s:
        await s.execute(
            text("delete from wecom_transfer_items where job_id = :j"), {"j": job_id}
        )
        await s.execute(text("delete from wecom_sync_jobs where id = :j"), {"j": job_id})
        await s.execute(text("delete from customer_owner_history where customer_id = :c"),
                        {"c": ids["customer"]})
        await s.execute(text("delete from customers where id = :c"), {"c": ids["customer"]})
        await s.execute(text("delete from user_roles where user_id in (:a, :b)"),
                        {"a": ids["leaver"], "b": ids["taker"]})
        await s.execute(text("delete from users where id in (:a, :b)"),
                        {"a": ids["leaver"], "b": ids["taker"]})
        await s.commit()


def assert_skip_taken_marking() -> None:
    """「跳过」要分清两种：**对象已被别人先动过** vs **本来就不用管**。

    第九批复审 P1 的收尾。并发下被同事接手的客户记成 `skipped`，而 `skipped`
    的中文标签是全站共用的一句「无需处理」——它同时罩着"客户已不存在"
    "跟进关系已不存在""本次未要求转接企微关系"这些**真的无事可做**的项。
    于是那笔被跳掉的客户：标签看不出差别，又因为跳过是终态、不进"未完成"清单，
    界面上**任何列表里都找不到它**，操作者只看到一个数字，不知道是哪几笔、
    留在了谁名下。

    这里钉两件事（都不碰 `crm_status` 本身：重试、汇总、索引都挂在它上面）：

      ① 序列化时把"被别人先动过"的那类单独标成 `crm_taken`，其余跳过不标、
         其它状态（已交接/冻结/失败）也不会被误标；
      ② 判据用的那份原因登记表与 `service.py` 的真实调用点**双向一致** ——
         改名时只改一边，这些项会悄悄退回「无需处理」，没有断言就查不出来。
    """
    import ast
    from pathlib import Path

    from app.modules.wecom.model import TRANSFER_SKIP_TAKEN_REASONS, WeComTransferItem
    from app.modules.wecom.service import serialize_transfer_item

    def payload(status: str, error: str | None) -> dict:
        # 不落库：只验序列化这一层的判定
        return serialize_transfer_item(
            WeComTransferItem(
                job_id=0, kind="customer", business_id=0, label="检查用",
                crm_status=status, wecom_status="not_applicable", crm_error=error,
            )
        )

    for reason in sorted(TRANSFER_SKIP_TAKEN_REASONS):
        check_true(f"「{reason}」标成已被他人先动过（crm_taken）",
                   payload("skipped", reason)["crm_taken"] is True, reason)
    for reason in ("客户已不存在", "跟进关系已不存在", "本次未要求转接企微关系"):
        check_true(f"「{reason}」仍是真·无需处理，不标 crm_taken",
                   payload("skipped", reason)["crm_taken"] is False, reason)
    for status in ("moved", "frozen", "failed", "pending", "not_applicable"):
        check_true(f"状态 {status} 不会被误标 crm_taken",
                   payload(status, "客户已由其他同事接手")["crm_taken"] is False, status)

    # ② 登记表 ↔ 调用点双向对账（判据是文案，最怕"只改一边"）
    src = Path(__file__).resolve().parents[1] / "app" / "modules" / "wecom" / "service.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    marked: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "_mark_crm"):
            continue
        args = node.args
        if len(args) < 4:
            continue
        status, error = args[2], args[3]
        if not (isinstance(status, ast.Constant) and status.value == "skipped"):
            continue
        # 第 4 个参数不一定是个直接量：首跑里"被同事接手 / 已回到公海"写成了
        # 三元表达式（`"客户已回到公海" if ... else "客户已由其他同事接手"`），
        # 只认 `ast.Constant` 会把首跑那一条整个漏掉（第九批复审收尾发现）。
        # 两个分支都要收进来。
        values: list[str] = []
        pending: list[ast.expr] = [error]
        while pending:
            node_expr = pending.pop()
            if isinstance(node_expr, ast.Constant) and isinstance(node_expr.value, str):
                values.append(node_expr.value)
            elif isinstance(node_expr, ast.IfExp):
                pending.extend((node_expr.body, node_expr.orelse))
        # ⚠️ 新增"被别人先动过"这类跳过原因时，登记表、service.py 的调用点，
        #    **以及下面这串子串**三处要同步，否则对账会红。
        for value in values:
            if (
                "已改，不再是离职人" in value
                or "已由其他同事接手" in value
                or "已回到公海" in value
            ):
                marked.add(value)

    check("「被别人先动过」的原因：登记表与 service.py 的真实调用点完全一致",
          sorted(marked), sorted(TRANSFER_SKIP_TAKEN_REASONS))


async def assert_restore_with_new_owner(ids: dict, admin_token: str) -> None:
    """**从回收站恢复**时指定新负责人，要和「转移负责人」办一样的事。

    复审复现的那个错：客户、报价、订单、未完成待办原本归离职人 A，删除后由管理员
    指定 B 恢复 —— 结果客户是归了 B，可他打开原报价/原订单是 **403**、客户下的待办是
    **空列表**，单据负责人还写着 A，客户归属历史里也没留痕。根因是恢复那条路只把
    `customer.owner_id` 一改了事，**没有走改派**。

    这一段接在第 5 段之后跑：此时客户归 `from`、单据在 `from` 名下，
    `order_colleague` / `doc_colleague` 在同事名下 —— 正是复审描述的局面。
    """
    print("\n── 11) 恢复时换负责人 = 一次改派（回收站复审第三轮）")
    cid = ids["customer"]

    async with SessionLocal() as s:
        history_before = int(
            (
                await s.execute(
                    text(
                        "select count(*) from customer_owner_history where customer_id = :c"
                    ),
                    {"c": cid},
                )
            ).scalar_one()
        )

    # ---- 11.1 先删掉它（走真实接口，真的进回收站）----
    status, res = call("DELETE", f"/customers/{cid}", token=admin_token)
    check_true("删客户成功（进回收站）", status == 200 and res.get("code") == 0,
               f"HTTP {status} {res.get('message')}")

    # ---- 11.2 恢复时指定新负责人 ----
    status, res = call("POST", f"/customers/{cid}/restore", admin_token,
                       {"owner_id": ids["to"]})
    check_true("恢复时指定新负责人成功", status == 200 and res.get("code") == 0,
               f"HTTP {status} {res.get('message')}")
    check_true("接口话术说明了负责人已一并换过来",
               "负责人" in (res.get("message") or ""), res.get("message"))
    st = await read_state(ids)

    check("客户负责人换成了指定那位", st["customer_owner"], ids["to"])
    check("原负责人名下的订单也接过去", st["order_mine_owner"], ids["to"])
    check("商机也接过去", st["opportunity_owner"], ids["to"])
    check("报价也接过去", st["quote_owner"], ids["to"])
    check("打样（跟单责任）也接过去", st["sample_owner"], ids["to"])
    check("打样（生产责任）也接过去", st["sample_production_owner"], ids["to"])
    check("没办完的待办也接过去", st["open_task_owner"], ids["to"])
    check("已完成的待办不动（历史记录要留档）", st["done_task_owner"], ids["from"])
    check("在职同事负责的订单不动", st["order_colleague_owner"], ids["colleague"])
    check("业绩归属一个字没动", st["order_mine_sales_owner"], ids["colleague"])
    check("历史创建人一个字没动", st["order_mine_created_by"], ids["colleague"])
    check("打样历史文件跟着跟单责任走", st["doc_mine_owner"], ids["to"])
    check("在职同事名下的文件不动", st["doc_colleague_owner"], ids["colleague"])
    check("客户归属历史新增了一条（恢复也必须写）",
          st["history_count"], history_before + 1)

    async with SessionLocal() as s:
        pool = (
            await s.execute(
                text("select pool_status from customers where id = :c"), {"c": cid}
            )
        ).scalar_one()
        restored = int(
            (
                await s.execute(
                    text(
                        "select count(*) from audit_logs where business_type = 'customer'"
                        " and business_id = :c and action = 'restore'"
                    ),
                    {"c": cid},
                )
            ).scalar_one()
        )
    check("指定负责人后是**私海**（不再出现在公海筛选里）", pool, "private")
    check_true("恢复审计仍然留着（没被改派那笔顶掉）", restored >= 1, f"{restored} 条")

    # ---- 11.3 接手人真的能用接口打开这些单据（复审那张表的正面）----
    to_token = login(ids["to_username"], PASSWORD)
    check("接手人能打开客户", call("GET", f"/customers/{cid}", to_token)[0], 200)
    check("接手人能打开原订单", call("GET", f"/orders/{ids['order_mine']}", to_token)[0], 200)
    check("接手人能打开原商机",
          call("GET", f"/opportunities/{ids['opportunity']}", to_token)[0], 200)
    check("接手人能打开原报价",
          call("GET", f"/quotes/{ids['quote']}", to_token)[0], 200)
    status, res = call("GET", f"/customers/{cid}/orders", to_token)
    check("接手人能看到客户下的订单（同事那张仍看不到）",
          sorted(row["id"] for row in items_of(res)), [ids["order_mine"]])
    status, res = call("GET", f"/customers/{cid}/tasks", to_token)
    check_true("接手人能看到客户下没办完的待办（从前是空列表）",
               ids["open_task"] in {row["id"] for row in items_of(res)},
               str(items_of(res)))

    # ---- 11.4 旧负责人已经看不到它了 ----
    from_token = login(ids["from_username"], PASSWORD)
    check("旧负责人打开原订单 → 403",
          call("GET", f"/orders/{ids['order_mine']}", from_token)[0], 403)

    # ---- 11.5 不换人的恢复：一个字段都不动，也不写归属历史 ----
    async with SessionLocal() as s:
        before_plain = int(
            (
                await s.execute(
                    text(
                        "select count(*) from customer_owner_history where customer_id = :c"
                    ),
                    {"c": cid},
                )
            ).scalar_one()
        )
    check("再删一次", call("DELETE", f"/customers/{cid}", token=admin_token)[0], 200)
    check_true("不指定负责人恢复 → 成功",
               call("POST", f"/customers/{cid}/restore", admin_token, {})[0] == 200)
    st = await read_state(ids)
    check("不换人：负责人不变", st["customer_owner"], ids["to"])
    check("不换人：单据不动", st["order_mine_owner"], ids["to"])
    check("不换人：不新增归属历史", st["history_count"], before_plain)


async def main() -> None:
    db_name = require_isolated_db()
    print(f"隔离库：{db_name}")
    await cleanup()

    admin_token = login("admin", "admin123")
    ids = await seed_fixtures()
    cid = ids["customer"]
    print(f"夹具：客户 #{cid}；订单 #{ids['order_mine']}（原负责人）/ "
          f"#{ids['order_colleague']}（在职同事负责）；"
          f"账号 {ids['from_username']} / {ids['to_username']}")

    # ── 1) 转移：原负责人 → 新负责人（两个都是"只管自己"的业务员）────────
    status, res = call(
        "POST", f"/customers/{cid}/transfer", admin_token,
        {"owner_id": ids["to"], "reason": "CHK 交接搬单据断言"},
    )
    check_true("转移接口成功", status == 200 and res.get("code") == 0,
               f"HTTP {status} {res.get('message')}")
    transferred = (res.get("data") or {}).get("document_transfer") or {}
    check_true("接口回了「单据跟着走」的结果（页面靠它提示）",
               "moved" in transferred and "skipped" in transferred, str(transferred))
    check("这次没有并发接走，跳过数为 0", transferred.get("skipped_total"), 0)
    check_true("确实搬过东西（moved_total > 0）",
               (transferred.get("moved_total") or 0) > 0, str(transferred))
    st = await read_state(ids)

    check("客户负责人已变", st["customer_owner"], ids["to"])
    check("原负责人名下的订单跟着到新负责人", st["order_mine_owner"], ids["to"])
    check("商机跟着走", st["opportunity_owner"], ids["to"])
    check("报价跟着走", st["quote_owner"], ids["to"])
    check("打样（跟单责任）跟着走", st["sample_owner"], ids["to"])
    check("打样（生产责任）跟着走", st["sample_production_owner"], ids["to"])
    check("没办完的待办跟着走", st["open_task_owner"], ids["to"])
    check("已完成的待办不动（历史记录要留档）", st["done_task_owner"], ids["from"])
    check("在职同事负责的订单不动", st["order_colleague_owner"], ids["colleague"])
    check("业绩归属不变", st["order_mine_sales_owner"], ids["colleague"])
    check("历史创建人不变", st["order_mine_created_by"], ids["colleague"])
    check_true("归属历史记了一条", st["history_count"] >= 1, f"{st['history_count']} 条")

    # ── 1b) 打样的**历史生成文件**（customer_id 为空，只挂来源打样单）──────
    # 这是返修点：文件按 `BizDoc.owner_id` 判可见性，漏迁的话接手人打开被 403。
    check("打样历史文件跟着跟单责任走（customer_id 为空也要兜住）",
          st["doc_mine_owner"], ids["to"])
    check("只变了生产责任的打样单：它的文件不跟着走（文件跟跟单责任）",
          st["doc_prod_only_owner"], ids["from"])
    check("在职同事名下的文件不动", st["doc_colleague_owner"], ids["colleague"])

    # ── 2) 新负责人用接口看：只看到搬过来的那一张 ────────────────────────
    to_token = login(ids["to_username"], PASSWORD)
    status, res = call("GET", f"/customers/{cid}/orders", to_token)
    check("新负责人只看到搬过来的那一张（同事那张仍看不到）",
          sorted(row["id"] for row in items_of(res)), [ids["order_mine"]])
    check_true("（上面这条接口确实通了）", status == 200, f"HTTP {status}")

    # ── 3) 旧负责人用订单明细接口看：已经不是他的了 ──────────────────────
    from_token = login(ids["from_username"], PASSWORD)
    status, _ = call("GET", f"/orders/{ids['order_mine']}", from_token)
    check("旧负责人看不到已经交出去的订单（订单明细按自己的负责人判范围）", status, 403)

    # ── 4) 放进公海：不搬 ───────────────────────────────────────────────
    status, res = call(
        "POST", f"/customers/{cid}/release-to-pool", admin_token,
        {"reason": "CHK 交接搬单据断言：放入公海"},
    )
    check_true("放入公海接口成功", status == 200 and res.get("code") == 0,
               f"HTTP {status} {res.get('message')}")
    st = await read_state(ids)
    check("放进公海后客户无负责人", st["customer_owner"], None)
    check("放进公海**不搬**：订单仍挂在最后经手人名下", st["order_mine_owner"], ids["to"])

    # ── 5) 从公海接走：按归属历史一并接过来 ─────────────────────────────
    status, res = call("POST", f"/public-pool/customers/{cid}/claim", from_token)
    check_true("公海领取接口成功", status == 200 and res.get("code") == 0,
               f"HTTP {status} {res.get('message')}")
    st = await read_state(ids)
    check("领取后客户归领取人", st["customer_owner"], ids["from"])
    check("领取时把历史单据一并接过来（取归属历史里的上一位负责人）",
          st["order_mine_owner"], ids["from"])
    check("接过来时业绩归属仍然不变", st["order_mine_sales_owner"], ids["colleague"])
    check("在职同事那一张还是不动", st["order_colleague_owner"], ids["colleague"])

    # ── 6) 两条交接路共用的类别名单不漂移 ───────────────────────────────
    missing = [kind for kind in DOCUMENT_KINDS if kind not in TRANSFER_KIND_LABEL]
    check("DOCUMENT_KINDS 里的类别都在离职交接的名单里", missing, [])
    # 提示语里的类别名也要跟着齐全（`biz_doc` 是本模块专有、不在离职交接名单里）
    from app.modules.customer.documents import DOCUMENT_KIND_LABEL

    check("DOCUMENT_KIND_LABEL 与 DOCUMENT_KINDS 对齐",
          sorted(set(DOCUMENT_KIND_LABEL) - set(DOCUMENT_KINDS)), ["biz_doc"])

    # ── 7) 并发：改派客户的同时同事把订单接走 —— 不许覆盖 ────────────────
    await assert_concurrent_takeover(ids, admin_token)

    # ── 8) 离职交接首跑的逐项搬运同样是"带条件的更新" ────────────────────
    await assert_handover_move_is_conditional(ids)

    # ── 9) 离职交接首跑真的会跳过"已被同事接手"的客户（第九批复审 P1）────
    #     调**完整** transfer_relations()：前面第 8 段只覆盖了搬运辅助函数，
    #     客户那一格没被照到，正是这次复验露出来的缺口。
    await assert_handover_first_run_skips_taken_customer()

    # ── 9b) 首跑同样跳过"已被放回公海"的客户（第九批复审收尾，2026-10-07）──
    #     与 9) 只差一处：并发进来的是"被主管放回公海"而不是"被同事接走"。
    await assert_handover_first_run_skips_pooled_customer()

    # ── 10) 「跳过」要分清"被同事先动过"与"本来就不用管" ─────────────────
    print("\n── 10) 跳过的分类（crm_taken）与原因登记表对账")
    assert_skip_taken_marking()

    # ── 11) 恢复时换负责人 = 一次改派（回收站复审第三轮）────────────────
    #     与第 1 段同一条口径，只是入口换成「回收站恢复」——
    #     从前它只改客户负责人，新人接手是个空壳（报价/订单 403、待办空列表）。
    await assert_restore_with_new_owner(ids, admin_token)

    await cleanup()

    print()
    if FAILURES:
        print(f"❌ 交接搬单据回归失败 {len(FAILURES)} 项：")
        for item in FAILURES:
            print(f"   - {item}")
        raise SystemExit(1)
    print("✅ 交接搬单据回归通过")


if __name__ == "__main__":
    asyncio.run(main())
