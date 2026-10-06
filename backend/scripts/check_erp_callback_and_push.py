#!/usr/bin/env python
"""ERP 回调与推单恢复：真 PostgreSQL 上的回归（第八批 §8.11 / §8.12）。

离线单测（`tests/test_erp_callback_and_push.py`）证明不了的三件事，只有真库能证明：

1. **事件键查重真的走 SQL**：查重条件是 JSONB 路径表达式
   （`response_data['event_key']`）。FakeSession 回答不了 sql，只有真库能证明
   它语法正确、也真的命中（离线测的是分支，不是这条 SQL）。
2. **双连接并发推单只调一次外部系统**：靠 `SELECT ... FOR UPDATE` +
   `populate_existing` 序列化。SQLite 直接忽略 FOR UPDATE，只有 PostgreSQL 有真行锁。
3. **readiness 的统计数字真的按数据范围过滤**：真 SQL 的 `in_(子查询)`。

跑法（`ops/iso_checks.ps1` 会建一次性库、迁移、播种、起隔离后端）：

    cd backend
    API_BASE=http://127.0.0.1:8013/api/v1 \\
      DATABASE_URL=postgresql+asyncpg://crm:...@127.0.0.1:5433/crm_iso_x \\
      PYTHONPATH=. .venv/bin/python scripts/check_erp_callback_and_push.py

⚠️ 必须显式给 API_BASE，且**不能**指向 8000（开发后端）：本套件会写库、会真的
发（假的）推送，漏传就会把夹具建在隔离库、请求打到开发后端。

外部系统一律用进程内替身（`FakeErp`）：不连聚水潭、不连 erp-bridge、不连钉钉。
"""

import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal

import app.main  # 副作用导入：保证所有模型都注册进 metadata（下面按 ORM 查询）
from sqlalchemy import select, text

from app.core.config import settings
from app.core.database import SessionLocal
from app.modules.customer.model import Customer
from app.modules.erp import service as svc
from app.modules.erp.adapter import ErpError
from app.modules.integration.model import ExternalMapping, IntegrationLog
from app.modules.order.model import SalesOrder
from app.modules.user.model import User

#: 显式引用一次上面那个副作用导入：pyflakes 不会把"导入了但没用到"报成问题
#: （`import app.main` 本身只为触发模型注册，不引用它静态检查必报未使用）。
MODELS_REGISTERED = app.main.__name__

BASE = os.environ.get("API_BASE", "")
FAILURES: list[str] = []
PREFIX = "CHKERP"
STAMP = str(int(time.time()))

if not BASE:
    raise SystemExit(
        "必须显式设置 API_BASE（本套件会写库并触发推送，不能默认打到开发后端 8000）"
    )
if "8000" in BASE:
    raise SystemExit(f"API_BASE 指向 8000（开发后端）很可能是误传：{BASE}")

IDS: dict = {}


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


# ---------------------------------------------------------------- 外部系统替身


class FakeErp:
    """ERP 替身：只在本进程内被调用，绝不发真实请求。"""

    system_type = "ERP"
    label = f"{PREFIX}假ERP"
    STATUS_MAP = {"Confirmed": "in_production", "Sent": "shipped", "Cancelled": "cancelled"}

    def __init__(self, *, external_id: str = "ERP-FAKE-1", delay: float = 0.0):
        self.external_id = external_id
        self.delay = delay
        self.calls: list[dict] = []

    async def push_order(self, payload):
        self.calls.append(payload)
        if self.delay:
            # 让另一条连接有机会走到 SELECT ... FOR UPDATE 上等锁
            await asyncio.sleep(self.delay)
        return {
            "external_id": self.external_id,
            "external_code": payload.get("so_id"),
            "raw": {"code": 0},
        }

    async def fetch_order_status(self, external_id):
        return {"status": None, "raw": {}}

    def missing_config(self):
        return []


_REAL_GET_ADAPTER = svc.get_adapter


def use_adapter(fake: FakeErp) -> None:
    svc.get_adapter = lambda *a, **k: fake


def restore_adapter() -> None:
    svc.get_adapter = _REAL_GET_ADAPTER


# ---------------------------------------------------------------- 夹具


async def cleanup() -> None:
    """自底向上清干净。日志/映射按本套件的前缀与订单 id 清。

    订单 id 是本套件自己造出来的整数，直接内联进 SQL（与 check_quote_api 同一做法）；
    用 `any(:ids)` 传数组在 asyncpg 上还要额外声明类型，反而更容易踩坑。
    """
    async with SessionLocal() as s:
        ids_sql = ",".join(str(int(value)) for value in (IDS.get("order_ids") or [0]))
        await s.execute(
            text(f"delete from order_status_history where order_id in ({ids_sql})")
        )
        await s.execute(
            text(
                f"delete from integration_logs where business_id in ({ids_sql}) "
                "or request_data->>'so_id' like :p "
                "or request_data->>'erp_order_id' like :p "
                "or request_data->>'order_no' like :p"
            ),
            {"p": f"{PREFIX}%"},
        )
        await s.execute(
            text(
                "delete from external_mappings where business_type = 'order' and "
                f"(internal_id in ({ids_sql}) or external_id like :p or external_code like :p)"
            ),
            {"p": f"{PREFIX}%"},
        )
        await s.execute(
            text("delete from sales_orders where order_no like :p"), {"p": f"{PREFIX}%"}
        )
        await s.execute(text("delete from customers where name like :p"), {"p": f"{PREFIX}%"})
        await s.commit()


async def _align_sequences(session) -> None:
    """把本套件要写入的自增序列校准到现有数据之后。

    ## 为什么必须做

    真库里已经出现过：`asyncpg.exceptions.UniqueViolationError: duplicate key value
    violates unique constraint "sales_orders_pkey"（Key (id)=(6) already exists）`。
    根因不是产品缺陷，而是**序列落后于数据**：只要有夹具（或 seed）用显式 id 插过行，
    PostgreSQL 的序列不会被推进，随后任何"交给自增"的插入都会拿到一个已被占用的 id
    并撞主键。本套件造 9 张单 + 日志 + 映射，全都依赖自增，所以先把序列对齐：
    `setval(seq, max(id)+1, false)` —— 既不改任何业务数据，也不和 seed 的 id 段抢
    （它只是把游标推到现有数据之后）。

    只校准本套件真正会插入的表。一次性隔离库，用完即删。
    """
    for table in (
        "customers",
        "sales_orders",
        "order_status_history",
        "integration_logs",
        "external_mappings",
    ):
        await session.execute(
            text(
                f"select setval(pg_get_serial_sequence('{table}', 'id'), "
                f"coalesce((select max(id) from {table}), 0) + 1, false)"
            )
        )


async def build_fixtures() -> None:
    async with SessionLocal() as s:
        users = {
            row.username: row.id
            for row in (
                await s.execute(select(User).where(User.username.in_(("admin", "zhangsan", "lisi"))))
            ).scalars().all()
        }
        for name in ("admin", "zhangsan", "lisi"):
            if name not in users:
                raise SystemExit(f"隔离库里没有种子账号 {name}，请先跑 scripts.seed")
        owner_a, owner_b = users["zhangsan"], users["lisi"]

        # 先对齐序列，再插任何夹具（顺序不能换：插完再对齐就晚了）
        await _align_sequences(s)

        customer = Customer(
            name=f"{PREFIX}客户-{STAMP}",
            owner_id=owner_a,
            pool_status="private",
            created_by=owner_a,
        )
        s.add(customer)
        await s.flush()

        def new_order(suffix: str, *, erp_order_id=None, owner=owner_a) -> SalesOrder:
            return SalesOrder(
                order_no=f"{PREFIX}-{suffix}-{STAMP}",
                customer_id=customer.id,
                owner_id=owner,
                sales_owner_id=owner,
                status="pending",
                total_amount=Decimal("100"),
                currency="CNY",
                erp_order_id=erp_order_id,
                created_by=owner_a,
            )

        orders = {
            "conflict": new_order("A", erp_order_id=f"ERP-A-{STAMP}"),
            "push": new_order("PUSH"),
            "no_credential": new_order("NOCRED"),
            "mapped": new_order("MAPPED"),
            "concurrent": new_order("CONC"),
            "unknown": new_order("UNK"),
            "http": new_order("HTTP"),
            "recover": new_order("RECOVER"),
            "outsider": new_order("OUT", owner=owner_b),
        }
        s.add_all(list(orders.values()))
        await s.flush()
        IDS.update(
            owner_a=owner_a,
            owner_b=owner_b,
            customer=customer.id,
            orders={key: row.id for key, row in orders.items()},
            order_nos={key: row.order_no for key, row in orders.items()},
            order_ids=[row.id for row in orders.values()],
        )
        await s.commit()


async def _order(session, key: str) -> SalesOrder:
    return await session.get(SalesOrder, IDS["orders"][key])


def _external(suffix: str) -> str:
    """外部单号。

    刻意**不等于**订单号（多了 EXT 段）：`_require_external_credential` 会把
    "外部单号 == 本地单号"判成没有凭据，夹具如果撞名就会把这条规则误触发。
    """
    return f"{PREFIX}EXT-{suffix}-{STAMP}"


# ---------------------------------------------------------------- 各项检查


async def check_webhook_fail_closed() -> None:
    """回调没有共享密钥时必须拒绝（保留已有的 fail-closed 守卫）。"""
    print("=== 1. 回调密钥守卫（HTTP，未配 ERP_WEBHOOK_SECRET）===")
    order_no = IDS["order_nos"]["conflict"]
    status, res = call(
        "POST",
        "/webhooks/erp/order-status",
        body={"order_no": order_no, "status": "Cancelled"},
    )
    check("未带密钥的回调被拒", status, 403)
    async with SessionLocal() as s:
        row = await _order(s, "conflict")
        check("被拒的回调没有改状态", row.status, "pending")
        logs = (
            await s.execute(
                text("select count(*) from integration_logs where business_id = :o"),
                {"o": row.id},
            )
        ).scalar()
        check("被拒的回调没有留可疑日志", logs, 0)


async def check_dual_number_conflict() -> None:
    """CRM-A 已绑 ERP-A，回调却给 ERP-B：拒绝改状态并进异常队列。"""
    print("=== 2. 双编号不同源（服务层 + 真库）===")
    fake = FakeErp()
    use_adapter(fake)
    try:
        async with SessionLocal() as s:
            order_no = IDS["order_nos"]["conflict"]
            result = await svc.apply_status_webhook(
                s,
                order_no=order_no,
                erp_order_id=f"ERP-B-{STAMP}",
                raw_status="Confirmed",
                remark="伪造的冲突回调",
                raw_event={"order_no": order_no, "o_id": f"ERP-B-{STAMP}", "status": "Confirmed"},
            )
            check("冲突事件不判定为已匹配", result["matched"], False)
            check("冲突事件明确回报", result.get("conflict"), True)
            row = await s.get(SalesOrder, IDS["orders"]["conflict"])
            check("冲突时订单状态不变", row.status, "pending")
            check("冲突时主表外部单号不变", row.erp_order_id, f"ERP-A-{STAMP}")
            log = (
                await s.execute(
                    select(IntegrationLog)
                    .where(IntegrationLog.status == "conflict")
                    .order_by(IntegrationLog.id.desc())
                )
            ).scalars().first()
            check_true("冲突进了异常队列（状态 conflict）", log is not None)
            check(
                "冲突记录保留了完整事件（含 raw_status 与备注）",
                (log.request_data.get("status"), log.request_data.get("remark")),
                ("Confirmed", "伪造的冲突回调"),
            )
            check_true("冲突记录保留了原始报文", isinstance(log.request_data.get("raw"), dict))
            check_true("冲突记录写明了两个编号各自指向谁", "ERP-B" in str(log.response_data))
            check(
                "冲突时没有写状态历史",
                (
                    await s.execute(
                        text("select count(*) from order_status_history where order_id = :o"),
                        {"o": IDS["orders"]["conflict"]},
                    )
                ).scalar(),
                0,
            )
    finally:
        restore_adapter()


async def check_event_replay_and_recovery() -> None:
    """未匹配留存完整事件；补映射后重放可恢复；再重放不重复写。"""
    print("=== 3. 事件键 / 未匹配恢复 / 重放去重（真库 JSONB 查询）===")
    fake = FakeErp()
    use_adapter(fake)
    external = _external("X")
    try:
        async with SessionLocal() as s:
            result = await svc.apply_status_webhook(
                s,
                order_no=None,
                erp_order_id=external,
                raw_status="Sent",
                remark="顺丰 SF123",
                raw_event={"o_id": external, "status": "Sent", "remark": "顺丰 SF123"},
            )
            check("未知外部单号判为未匹配", result.get("unmatched"), True)
            key = result["event_key"]
            log = (
                await s.execute(
                    select(IntegrationLog).where(IntegrationLog.status == "unmatched")
                )
            ).scalars().first()
            check(
                "未匹配事件保存了完整业务字段",
                (log.request_data.get("status"), log.request_data.get("remark")),
                ("Sent", "顺丰 SF123"),
            )
            check("未匹配事件的键稳定可查", log.response_data.get("event_key"), key)

        # 事后补映射（模拟"先未匹配、后补映射"）
        async with SessionLocal() as s:
            s.add(
                ExternalMapping(
                    system_type="ERP",
                    business_type="order",
                    internal_id=IDS["orders"]["recover"],
                    external_id=external,
                    external_code=IDS["order_nos"]["recover"],
                )
            )
            await s.commit()

        async with SessionLocal() as s:
            before = (
                await s.execute(
                    text(
                        "select count(*) from integration_logs "
                        "where response_data->>'event_key' = :k"
                    ),
                    {"k": key},
                )
            ).scalar()
            result = await svc.apply_status_webhook(
                s, order_no=None, erp_order_id=external, raw_status="Sent", remark="顺丰 SF123"
            )
            check("补上映射后重放能恢复处理", result["matched"], True)
            check("重放真的推进了状态", result["changed"], True)
            row = await s.get(SalesOrder, IDS["orders"]["recover"])
            check("订单状态按重放事件更新", row.status, "shipped")
            superseded = (
                await s.execute(
                    text(
                        "select count(*) from integration_logs "
                        "where status = 'superseded' and response_data->>'event_key' = :k"
                    ),
                    {"k": key},
                )
            ).scalar()
            check("旧的未匹配记录被标成已被取代", superseded, 1)

            after = (
                await s.execute(
                    text(
                        "select count(*) from integration_logs "
                        "where response_data->>'event_key' = :k"
                    ),
                    {"k": key},
                )
            ).scalar()
            check("一次恢复只多写一条处理记录", after - before, 1)

            # 第三次：同事件重放（走 JSONB 路径查重）
            result = await svc.apply_status_webhook(
                s, order_no=None, erp_order_id=external, raw_status="Sent", remark="顺丰 SF123"
            )
            check("同事件重放被识别", result.get("replayed"), True)
            again = (
                await s.execute(
                    text(
                        "select count(*) from integration_logs "
                        "where response_data->>'event_key' = :k"
                    ),
                    {"k": key},
                )
            ).scalar()
            check("重放不再写日志", again, after)
    finally:
        restore_adapter()


async def check_push_credential_and_recovery() -> None:
    """没有外部受理凭据不能标已同步；重启后按映射恢复；结果未知要人核对。"""
    print("=== 4. 推单凭据 / 重启恢复 / 结果未知（服务层 + 真库）===")
    # 4.1 响应里没有外部单号
    fake = FakeErp(external_id="")
    use_adapter(fake)
    try:
        async with SessionLocal() as s:
            row = await _order(s, "no_credential")
            try:
                await svc.push_order(s, order=row, operator_id=IDS["owner_a"])
                check_true("空外部单号必须抛结果未知", False)
            except ErpError as error:
                check("空外部单号判为结果未知", getattr(error, "kind", None), "result_unknown")
            await s.rollback()
        async with SessionLocal() as s:
            row = await _order(s, "no_credential")
            check("没有凭据时不得写外部单号", row.erp_order_id, None)
            mapping_count = (
                await s.execute(
                    text(
                        "select count(*) from external_mappings where business_type = 'order' "
                        "and internal_id = :o"
                    ),
                    {"o": row.id},
                )
            ).scalar()
            check("没有凭据时不得建映射", mapping_count, 0)
            unknown = (
                await s.execute(
                    text(
                        "select count(*) from integration_logs where business_id = :o "
                        "and status = 'unknown'"
                    ),
                    {"o": row.id},
                )
            ).scalar()
            check("结果未知必须持久化（status=unknown）", unknown, 1)

        # 4.2 结果未知之后再推：必须先核对，不得盲目重推
        fake2 = FakeErp(external_id=_external("OK"))
        use_adapter(fake2)
        async with SessionLocal() as s:
            row = await _order(s, "no_credential")
            try:
                await svc.push_order(s, order=row, operator_id=IDS["owner_a"])
                check_true("结果未知未了结时必须拒绝重推", False)
            except ErpError as error:
                check("未了结的未知请求挡住重推", getattr(error, "kind", None), "result_unknown")
            await s.rollback()
        check("挡住重推时没有真的调用外部系统", len(fake2.calls), 0)

        # 4.3 人工核对后登记 → 再推直接返回已同步
        async with SessionLocal() as s:
            row = await _order(s, "no_credential")
            result = await svc.reconcile_push(
                s, order=row, external_id=_external("OK"), operator_id=IDS["owner_a"]
            )
            check("核对登记成功", result["reconciled"], True)
            check_true("核对登记了外部单号", result["erp_order_id"] == _external("OK"))
            check_true("核对时了结了未完成请求", result["cleared"] >= 1)
        calls_before = len(fake2.calls)
        async with SessionLocal() as s:
            row = await _order(s, "no_credential")
            result = await svc.push_order(s, order=row, operator_id=IDS["owner_a"])
            check("核对之后重推返回已同步", result.get("already_synced"), True)
        check("核对之后不再调用外部系统", len(fake2.calls), calls_before)

        # 4.4 成功推送：写映射 + 主表 + 状态历史
        fake3 = FakeErp(external_id=_external("PUSHED"))
        use_adapter(fake3)
        async with SessionLocal() as s:
            row = await _order(s, "push")
            result = await svc.push_order(s, order=row, operator_id=IDS["owner_a"])
            check("推送成功", result["pushed"], True)
            check("主表写的是外部单号", result["erp_order_id"], _external("PUSHED"))
        async with SessionLocal() as s:
            row = await _order(s, "push")
            check("主表落库", row.erp_order_id, _external("PUSHED"))
            mapping = (
                await s.execute(
                    select(ExternalMapping).where(
                        ExternalMapping.business_type == "order",
                        ExternalMapping.internal_id == row.id,
                    )
                )
            ).scalars().first()
            check_true("映射落库且与主表一致", mapping is not None and mapping.external_id == row.erp_order_id)
            history = (
                await s.execute(
                    text("select count(*) from order_status_history where order_id = :o"),
                    {"o": row.id},
                )
            ).scalar()
            check("写了状态历史", history, 1)

        # 4.5 "重启"：映射已有外部单号、主表为空 → 只补主表，不再建单
        async with SessionLocal() as s:
            row = await _order(s, "mapped")
            row_id = row.id
            s.add(
                ExternalMapping(
                    system_type="ERP",
                    business_type="order",
                    internal_id=row_id,
                    external_id=_external("MAPPED"),
                    external_code=row.order_no,
                )
            )
            await s.commit()
        fake4 = FakeErp(external_id=_external("SHOULD-NOT-BE-USED"))
        use_adapter(fake4)
        async with SessionLocal() as s:  # 新会话 = 进程重启后的第一次请求
            row = await _order(s, "mapped")
            result = await svc.push_order(s, order=row, operator_id=IDS["owner_a"])
            check("已有映射时判为已同步", result.get("already_synced"), True)
            check("已有映射时标记为补齐恢复", result.get("recovered"), True)
        check("已有映射时不再调用外部系统", len(fake4.calls), 0)
        async with SessionLocal() as s:
            row = await _order(s, "mapped")
            check("主表按映射补齐", row.erp_order_id, _external("MAPPED"))
    finally:
        restore_adapter()


async def check_concurrent_push() -> None:
    """两个连接同时推同一张单：只能调用一次外部系统，只留一行映射。"""
    print("=== 5. 双连接并发推单（真行锁）===")
    fake = FakeErp(external_id=_external("CONC"), delay=0.6)
    use_adapter(fake)
    try:

        async def _one(tag: str):
            async with SessionLocal() as s:
                row = await _order(s, "concurrent")
                try:
                    return tag, await svc.push_order(s, order=row, operator_id=IDS["owner_a"])
                except ErpError as error:
                    await s.rollback()
                    return tag, {"error": str(error)}

        results = await asyncio.gather(_one("A"), _one("B"))
    finally:
        restore_adapter()
    print(f"    两路结果：{[(tag, res.get('already_synced', res.get('error'))) for tag, res in results]}")
    check("并发推单只调用一次外部系统", len(fake.calls), 1)
    check_true(
        "两路都不算失败",
        all("error" not in res for _, res in results),
        str(results),
    )
    check(
        "其中只有一路真的推了，另一路回报已同步",
        sum(1 for _, res in results if res.get("already_synced") is True),
        1,
    )
    async with SessionLocal() as s:
        row = await _order(s, "concurrent")
        check("并发后主表只有一个外部单号", row.erp_order_id, _external("CONC"))
        mappings = (
            await s.execute(
                text(
                    "select count(*) from external_mappings where business_type = 'order' "
                    "and internal_id = :o"
                ),
                {"o": row.id},
            )
        ).scalar()
        check("并发只留一行映射", mappings, 1)
        success = (
            await s.execute(
                text(
                    "select count(*) from integration_logs where business_id = :o "
                    "and status = 'success' and direction = 'outbound'"
                ),
                {"o": row.id},
            )
        ).scalar()
        check("并发只留一条成功推送记录", success, 1)


async def check_readiness_state_and_scope() -> None:
    """三个变量齐全也只是"已配置未验证"；统计按数据范围给。"""
    print("=== 6. 接入状态如实显示 + 统计按权限（§8.12）===")
    saved = (
        settings.erp_provider,
        settings.erp_base_url,
        settings.erp_app_key,
        settings.erp_app_secret,
    )
    try:
        settings.erp_provider = "jushuitan"
        settings.erp_base_url = "https://example.invalid"
        settings.erp_app_key = "key"
        settings.erp_app_secret = "secret"
        class _User:
            def __init__(self, uid, scope, roles=()):
                self.id = uid
                self.data_scope = scope
                self.roles = list(roles)

            def has(self, code):
                return "admin" in self.roles

        async with SessionLocal() as s:
            admin_view = await svc.readiness(s, user=_User(IDS["owner_a"], "all", ["admin"]))
            owner_view = await svc.readiness(s, user=_User(IDS["owner_a"], "self"))
            total_orders = (await s.execute(text("select count(*) from sales_orders"))).scalar()
            own_orders = (
                await s.execute(
                    text("select count(*) from sales_orders where owner_id = :u"),
                    {"u": IDS["owner_a"]},
                )
            ).scalar()

        check("配置齐全也不显示已接通", admin_view["connected"], False)
        check("配置齐全的接入状态是已配置未验证", admin_view["state"], "configured_unverified")
        check("写入能力未验收", admin_view["capabilities"]["write"]["verified"], False)
        check_true("开发环境显式标模拟", admin_view["simulated"] is True)
        check("管理员看到全量订单数", admin_view["counts"]["orders"], total_orders)
        check_true("管理员拿到配置明细", "missing" in admin_view.get("diagnostics", {}))
        check("普通查看者只看到自己范围内的订单数", owner_view["counts"]["orders"], own_orders)
        check_true(
            "普通查看者看不到配置明细",
            "missing" not in owner_view.get("diagnostics", {}),
        )
        check_true("普通查看者的结果标了范围受限", owner_view["scope"]["limited"] is True)
        check_true(
            "接口返回里没有密钥本身（只有布尔与结论）",
            "secret" not in json.dumps(owner_view, ensure_ascii=False).lower()
            and "app_key" not in owner_view,
        )
    finally:
        (
            settings.erp_provider,
            settings.erp_base_url,
            settings.erp_app_key,
            settings.erp_app_secret,
        ) = saved


async def check_http_endpoints(admin_token: str, owner_token: str) -> None:
    """接口层：未配置推送不标已同步；readiness 按角色给；异常队列看得到。"""
    print("=== 7. 接口层（HTTP）===")
    status, res = call("GET", "/integrations/erp/readiness", owner_token)
    check("普通查看者能读 readiness", status, 200)
    check("未配置时如实报未配置", (res.get("data") or {}).get("state"), "not_configured")
    check("接口 ready 不代表接通", (res.get("data") or {}).get("connected"), False)

    status, res = call("GET", "/integrations/erp/readiness", admin_token)
    admin_counts = ((res.get("data") or {}).get("counts") or {}).get("orders")
    status2, res2 = call("GET", "/integrations/erp/readiness", owner_token)
    owner_counts = ((res2.get("data") or {}).get("counts") or {}).get("orders")
    check_true(
        "普通查看者的统计不超过管理员的全量统计",
        admin_counts is not None and owner_counts is not None and owner_counts <= admin_counts,
        f"admin={admin_counts} owner={owner_counts}",
    )

    # 未配置时按订单 id 推送：必须 422 且**不**把订单标成已推送
    order_id = IDS["orders"]["http"]
    status, res = call("POST", f"/integrations/erp/orders/{order_id}/sync", owner_token)
    check("未配置时推送返回 422", status, 422)
    async with SessionLocal() as s:
        row = await s.get(SalesOrder, order_id)
        check("未配置时订单没有被标成已推送", row.erp_order_id, None)

    status, res = call("GET", "/integrations/erp/exception-queue?page_size=200", admin_token)
    check("异常队列可读", status, 200)
    items = (res.get("data") or {}).get("items") or []
    kinds = {item.get("status") for item in items}
    check_true(
        "异常队列里能看到冲突/未匹配/未发出的留痕",
        {"conflict", "unmatched", "not_sent", "skipped"} & kinds != set(),
        f"状态集合={sorted(k for k in kinds if k)}",
    )
    check_true(
        "异常队列条目带着完整事件与事件键",
        any(item.get("processing_status") and item.get("request_data") for item in items),
    )


async def main() -> int:
    admin_token = login("admin", "admin123")
    owner_token = login("zhangsan", "123456")
    print(f"已登录 admin / zhangsan（套件前缀 {PREFIX}，时间戳 {STAMP}）")
    await cleanup()
    await build_fixtures()
    try:
        await check_webhook_fail_closed()
        await check_dual_number_conflict()
        await check_event_replay_and_recovery()
        await check_push_credential_and_recovery()
        await check_concurrent_push()
        await check_readiness_state_and_scope()
        await check_http_endpoints(admin_token, owner_token)
    finally:
        restore_adapter()
        await cleanup()
    print()
    if FAILURES:
        print(f"FAILED {len(FAILURES)} 项：" + "、".join(FAILURES))
        return 1
    print("ERP 回调/推单/接入状态回归 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
