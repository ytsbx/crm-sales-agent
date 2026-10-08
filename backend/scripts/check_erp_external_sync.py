#!/usr/bin/env python
"""外部只读采集与对账：真 PostgreSQL 上的隔离回归（第八批 §8.13）。

离线单测（`tests/test_erp_external_sync.py`）证明不了的四件事，只有真库能证明：

1. **表结构与唯一约束真的建得出来**：新承载表（水位/原始事实/对象映射/来源台账/
   对账批次/差异队列）在 PostgreSQL 上的 DDL 与唯一键可用。⚠️ 本轮按交接约定
   **不改 alembic/versions**，所以本脚本会在**隔离库**里用 `create_all`
   临时建这几张表（`checkfirst=True`，幂等），只为验证；生产迁移由负责人统一写，
   建表要点见交接回报。
2. **唯一约束真的挡重复**：同一 (系统, 店铺, 对象类型, 稳定键) 插两次会冲突，
   而不是"服务层看着像去重了"。
3. **双连接并发采集只放行一个**：`SELECT ... FOR UPDATE` 抢水位行，
   另一个连接必须拿到 409（SQLite 直接忽略 FOR UPDATE，只有 PG 有真行锁）。
4. **JSONB 证据与差异查询**：差异的 `evidence` / `current_value` 在 PG 上是 JSONB，
   按 diff_key 幂等 upsert 的 SQL 真的能跑。

跑法（`ops/iso_checks.ps1` 会建一次性库、迁移、播种、起隔离后端）：

    cd backend
    API_BASE=http://127.0.0.1:8024/api/v1 \
      DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5433/crm_iso_ext \
      PYTHONPATH=. .venv/Scripts/python.exe scripts/check_erp_external_sync.py

⚠️ 必须显式给 API_BASE，且**不能**指向 8000（开发后端）：本套件会写库。

外部系统一律用进程内替身（`FakeCollectAdapter`）：不连聚水潭、不连 erp-bridge。
"""

import asyncio
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from _test_support import require_api_base, require_isolated_db

require_isolated_db()

import app.main  # noqa: F401  保证所有模型都注册进 metadata
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.base import Base
from app.core.config import settings
from app.core.database import SessionLocal
from app.modules.customer.model import Customer
from app.modules.erp import collection as col
from app.modules.erp import reconcile as rec
from app.modules.erp.adapter import ExternalPage
from app.modules.integration.model import (
    ExternalObjectMapping,
    ExternalRecord,
    ExternalSourceRegistry,
    ExternalSyncWatermark,
    IntegrationDiff,
    ReconciliationRun,
)
from app.modules.order.model import SalesOrder
from app.modules.product.model import Product, Sku, SkuFieldAuthority, SkuIdentitySource, SkuMasterVersion
from app.modules.user.model import User

BASE = require_api_base()
FAILURES: list[str] = []
PREFIX = "CHKCOLLECT"
STAMP = str(int(time.time()))
PERIOD_START = datetime(2026, 9, 1, tzinfo=UTC)
PERIOD_END = datetime(2026, 10, 1, tzinfo=UTC)

if not BASE:
    raise SystemExit("必须显式设置 API_BASE（本套件会写库，不能默认打到开发后端 8000）")
if "8000" in BASE:
    raise SystemExit(f"API_BASE 指向 8000（开发后端）很可能是误传：{BASE}")

#: 本轮新增、**还没有 alembic 迁移**的承载表。只在隔离库里临时建出来验证。
NEW_TABLES = [
    ExternalSyncWatermark.__table__,
    ExternalRecord.__table__,
    ExternalObjectMapping.__table__,
    ExternalSourceRegistry.__table__,
    ReconciliationRun.__table__,
    IntegrationDiff.__table__,
    SkuIdentitySource.__table__,
    SkuFieldAuthority.__table__,
    SkuMasterVersion.__table__,
]

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
        with urllib.request.urlopen(req, timeout=60) as resp:
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


# ---------------------------------------------------------------- 临时建表


async def ensure_tables() -> None:
    """在**隔离库**里临时建本轮新增的表（幂等）。

    为什么必须在这里做：迁移由负责人统一写（交接约定不动 alembic/versions），
    而这个脚本要验证的是"表结构与约束真的能在 PostgreSQL 上成立"。
    用 create_all(checkfirst=True) 不会碰已有表。
    """
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: Base.metadata.create_all(
                    sync_conn, tables=NEW_TABLES, checkfirst=True
                )
            )
    finally:
        await engine.dispose()


# ---------------------------------------------------------------- 外部系统替身


class FakeCollectAdapter:
    """只读采集替身：按游标给页。**绝不发真实请求。**"""

    system_type = "ERP"
    label = f"{PREFIX}假采集ERP"

    def __init__(self, pages, *, delay: float = 0.0):
        self.pages = list(pages)
        self.calls: list[dict] = []
        self.delay = delay

    def collection_object_types(self):
        return ("order", "shipment", "aftersale")

    async def fetch_records(self, *, object_type, shop_id, cursor=None, page_size=50):
        self.calls.append({"object_type": object_type, "shop_id": shop_id, "cursor": cursor})
        if self.delay:
            # 让另一条连接有机会走到 SELECT ... FOR UPDATE 上等锁
            await asyncio.sleep(self.delay)
        index = 0 if cursor is None else int(cursor.split("-")[1]) - 1
        items = self.pages[index] if 0 <= index < len(self.pages) else []
        has_more = index + 1 < len(self.pages)
        return ExternalPage(
            items=items,
            next_page_token=f"cursor-{index + 2}" if has_more else None,
            has_more=has_more,
            watermark=f"2026-09-30T00:00:0{index}",
        )


def _order_record(key: str, crm_no: str, amount: str = "100") -> dict:
    return {
        "object_type": "order",
        "external_id": key,
        "external_code": crm_no,
        "amount": amount,
        "currency": "CNY",
        "occurred_at": "2026-09-05T09:00:00",
        "payload": {"o_id": key, "so_id": crm_no, "pay_amount": amount},
    }


# ---------------------------------------------------------------- 夹具


async def cleanup() -> None:
    """自底向上清干净（只删本套件前缀造出来的行）。"""
    async with SessionLocal() as s:
        ids_sql = ",".join(str(int(value)) for value in (IDS.get("order_ids") or [0]))
        await s.execute(
            text("delete from integration_diffs where external_id like :p or evidence::text like :p"),
            {"p": f"%{PREFIX}%"},
        )
        await s.execute(text("delete from reconciliation_runs where run_key like :p"), {"p": f"%{PREFIX}%"})
        await s.execute(text("delete from external_records where dedupe_key like :p"), {"p": f"%{PREFIX}%"})
        await s.execute(
            text("delete from external_object_mappings where external_id like :p"), {"p": f"%{PREFIX}%"}
        )
        await s.execute(
            text("delete from external_sync_watermarks where shop_id like :p"), {"p": f"%{PREFIX}%"}
        )
        await s.execute(
            text("delete from external_source_registry where shop_id like :p"), {"p": f"%{PREFIX}%"}
        )
        await s.execute(text("delete from sku_identity_sources where external_code like :p"), {"p": f"%{PREFIX}%"})
        await s.execute(
            text(
                "delete from sku_field_authorities where sku_id in "
                f"(select id from skus where sku_code like :p)"
            ),
            {"p": f"%{PREFIX}%"},
        )
        await s.execute(
            text(
                "delete from sku_master_versions where sku_id in "
                f"(select id from skus where sku_code like :p)"
            ),
            {"p": f"%{PREFIX}%"},
        )
        await s.execute(text("delete from skus where sku_code like :p"), {"p": f"%{PREFIX}%"})
        await s.execute(text("delete from products where name like :p"), {"p": f"%{PREFIX}%"})
        await s.execute(
            text(
                "delete from integration_logs where business_type = 'external_collect' "
                "and response_data->>'shop_id' like :p"
            ),
            {"p": f"%{PREFIX}%"},
        )
        await s.execute(text("delete from sales_orders where order_no like :p"), {"p": f"%{PREFIX}%"})
        await s.execute(text("delete from customers where name like :p"), {"p": f"%{PREFIX}%"})
        if ids_sql != "0":
            await s.execute(
                text(f"delete from integration_logs where business_id in ({ids_sql})")
            )
        await s.commit()


async def build_fixtures() -> None:
    async with SessionLocal() as s:
        owner = (
            await s.execute(select(User).where(User.username == "zhangsan"))
        ).scalars().first()
        if owner is None:
            raise SystemExit("隔离库里没有种子账号 zhangsan，请先跑 scripts.seed")

        customer = Customer(
            name=f"{PREFIX}客户-{STAMP}",
            owner_id=owner.id,
            pool_status="private",
            created_by=owner.id,
        )
        s.add(customer)
        await s.flush()
        order = SalesOrder(
            order_no=f"{PREFIX}-O1-{STAMP}",
            customer_id=customer.id,
            owner_id=owner.id,
            sales_owner_id=owner.id,
            status="pending",
            total_amount=Decimal("100"),
            currency="CNY",
            created_by=owner.id,
        )
        product = Product(name=f"{PREFIX}产品-{STAMP}")
        s.add_all([order, product])
        await s.flush()
        sku = Sku(product_id=product.id, sku_code=f"{PREFIX}-SKU-{STAMP}", name=f"{PREFIX}件", unit="件")
        s.add(sku)
        await s.flush()
        IDS.update(
            owner=owner.id,
            customer=customer.id,
            order=order.id,
            order_no=order.order_no,
            product=product.id,
            sku=sku.id,
            shop=f"{PREFIX}-SHOP-A-{STAMP}",
        )
        await s.commit()


# ---------------------------------------------------------------- 各项检查


async def check_uniqueness_constraints() -> None:
    """唯一约束真的挡重复：同 (系统, 店铺, 对象类型, 稳定键) 插两次必须冲突。"""
    print("=== 1. 唯一约束（真 DDL）===")
    stamp = datetime.now(UTC)
    async with SessionLocal() as s:
        s.add(
            ExternalRecord(
                system_type="ERP",
                shop_id=IDS["shop"],
                object_type="order",
                dedupe_key=f"{PREFIX}-UNIQ",
                external_id=f"{PREFIX}-UNIQ",
                raw_digest="x",
                first_seen_at=stamp,
                last_seen_at=stamp,
            )
        )
        await s.commit()
    async with SessionLocal() as s:
        s.add(
            ExternalRecord(
                system_type="ERP",
                shop_id=IDS["shop"],
                object_type="order",
                dedupe_key=f"{PREFIX}-UNIQ",
                external_id=f"{PREFIX}-UNIQ",
                raw_digest="y",
                first_seen_at=stamp,
                last_seen_at=stamp,
            )
        )
        failed = False
        try:
            await s.commit()
        except Exception as error:  # noqa: BLE001 - 就是要看它冲突
            failed = True
            await s.rollback()
            print(f"    冲突信息：{type(error).__name__}")
    check_true("同一稳定键第二次插入被唯一约束挡下", failed)

    # 两店同编号必须都能插进去（不串）
    async with SessionLocal() as s:
        for shop in (f"{PREFIX}-SHOP-X-{STAMP}", f"{PREFIX}-SHOP-Y-{STAMP}"):
            s.add(
                ExternalRecord(
                    system_type="ERP",
                    shop_id=shop,
                    object_type="order",
                    dedupe_key=f"{PREFIX}-DUAL",
                    external_id=f"{PREFIX}-DUAL",
                    raw_digest="z",
                    first_seen_at=stamp,
                    last_seen_at=stamp,
                )
            )
        ok_two_shops = True
        try:
            await s.commit()
        except Exception as error:  # noqa: BLE001
            ok_two_shops = False
            await s.rollback()
            print(f"    两店插入失败：{error}")
    check_true("两个店铺的同编号能各存一行（店铺参与唯一键）", ok_two_shops)


async def check_repeat_collection_is_idempotent() -> None:
    """真库上重复采集：只有一行事实，第二次计成重复。"""
    print("=== 2. 重复拉取幂等（真库）===")
    page = [_order_record(f"{PREFIX}-EXT-1", IDS["order_no"])]
    adapter = FakeCollectAdapter([page])
    async with SessionLocal() as s:
        first = await col.collect_once(
            s, object_type="order", adapter=adapter, shop_id=IDS["shop"]
        )
        second = await col.collect_once(
            s, object_type="order", adapter=adapter, shop_id=IDS["shop"]
        )
    check("第一次是新增", first["inserted"], 1)
    check("第二次算重复", second["duplicates"], 1)
    async with SessionLocal() as s:
        count = (
            await s.execute(
                text(
                    "select count(*) from external_records where shop_id = :s and dedupe_key = :k"
                ),
                {"s": IDS["shop"], "k": f"{PREFIX}-EXT-1"},
            )
        ).scalar()
    check("库里仍然只有一行", count, 1)


async def check_pagination_break_resumes() -> None:
    """真库上的断点续拉：第 2 页失败留断点，续拉后三页齐全。"""
    print("=== 3. 分页断点续拉（真库）===")
    shop = f"{PREFIX}-SHOP-PAGE-{STAMP}"
    pages = [
        [_order_record(f"{PREFIX}-P1", IDS["order_no"])],
        [_order_record(f"{PREFIX}-P2", IDS["order_no"])],
        [_order_record(f"{PREFIX}-P3", IDS["order_no"])],
    ]

    class _Broken(FakeCollectAdapter):
        def __init__(self, pages):
            super().__init__(pages)
            self.failed = False

        async def fetch_records(self, *, object_type, shop_id, cursor=None, page_size=50):
            if cursor == "cursor-2" and not self.failed:
                self.failed = True
                raise RuntimeError("模拟分页中断")
            return await super().fetch_records(
                object_type=object_type, shop_id=shop_id, cursor=cursor, page_size=page_size
            )

    adapter = _Broken(pages)
    async with SessionLocal() as s:
        try:
            await col.collect_once(s, object_type="order", adapter=adapter, shop_id=shop)
            check_true("第一次应该失败", False)
        except RuntimeError:
            check_true("第一次采集按预期中断", True)
    async with SessionLocal() as s:
        cursor = (
            await s.execute(
                select(ExternalSyncWatermark).where(ExternalSyncWatermark.shop_id == shop)
            )
        ).scalars().first()
        check("中断后状态是 failed", cursor.status, "failed")
        check_true("断点被保留", bool(cursor.page_token), f"page_token={cursor.page_token}")
        resumed = await col.collect_once(s, object_type="order", adapter=adapter, shop_id=shop)
    check("续拉后跑完", resumed["status"], "idle")
    async with SessionLocal() as s:
        keys = (
            await s.execute(
                text(
                    "select dedupe_key from external_records where shop_id = :s order by dedupe_key"
                ),
                {"s": shop},
            )
        ).scalars().all()
    check("三页数据齐全且不重复", list(keys), [f"{PREFIX}-P1", f"{PREFIX}-P2", f"{PREFIX}-P3"])


async def check_concurrent_collect_uses_row_lock() -> None:
    """双连接同时采同一范围：只能有一个真的在采，另一个拿到 409。"""
    print("=== 4. 并发采集租约（真行锁）===")
    shop = f"{PREFIX}-SHOP-CONC-{STAMP}"
    adapter = FakeCollectAdapter(
        [[_order_record(f"{PREFIX}-C1", IDS["order_no"])]], delay=0.8
    )

    async def _one(tag: str):
        async with SessionLocal() as s:
            try:
                result = await col.collect_once(
                    s, object_type="order", adapter=adapter, shop_id=shop
                )
                return tag, result.get("status")
            except Exception as error:  # noqa: BLE001 - 另一路应当是"正在采集"
                await s.rollback()
                return tag, f"{type(error).__name__}: {error}"

    results = await asyncio.gather(_one("A"), _one("B"))
    print(f"    两路结果：{results}")
    rejected = [value for _, value in results if "正在采集" in str(value)]
    finished = [value for _, value in results if value == "idle"]
    check("只有一路真的采完", len(finished), 1)
    check_true("另一路被明确拒绝（不是静默并发）", len(rejected) == 1, str(results))
    # 采集只成功一路，所以外部只该被调一轮（一轮里可能有多页）
    check("外部只被调用一轮", adapter.calls.count({"object_type": "order", "shop_id": shop, "cursor": None}), 1)


async def check_reconciliation_and_evidence() -> None:
    """真库对账：差异落地、JSONB 证据取回、重复对账幂等。"""
    print("=== 5. 对账与原始证据（真库 / JSONB）===")
    shop = f"{PREFIX}-SHOP-REC-{STAMP}"
    missing_key = f"{PREFIX}-MISSING"
    adapter = FakeCollectAdapter(
        [
            [
                {
                    "object_type": "order",
                    "external_id": missing_key,
                    "external_code": f"{PREFIX}-NOT-EXIST",
                    "amount": "88",
                    "currency": "CNY",
                    "occurred_at": "2026-09-06T09:00:00",
                    "payload": {"o_id": missing_key, "so_id": f"{PREFIX}-NOT-EXIST"},
                }
            ]
        ]
    )
    async with SessionLocal() as s:
        await col.collect_once(s, object_type="order", adapter=adapter, shop_id=shop)
        first = await rec.run_reconciliation(
            s,
            period_start=PERIOD_START.date(),
            period_end=PERIOD_END.date(),
            shop_id=shop,
        )
        second = await rec.run_reconciliation(
            s,
            period_start=PERIOD_START.date(),
            period_end=PERIOD_END.date(),
            shop_id=shop,
        )
    check("第一次出了一个缺本地的差异", first["counters"]["missing_local"], 1)
    check("同一期间重复对账复用批次", second["run"]["id"], first["run"]["id"])
    async with SessionLocal() as s:
        diffs, total = await rec.list_diffs(s, diff_type="missing_local", shop_id=shop)
        check("差异只有一条", total, 1)
        detail = await rec.diff_evidence(s, await rec.get_diff(s, diffs[0]["id"]))
    check_true("差异详情能取回原始报文", bool(detail["records"]), str(detail["missing_records"]))
    check("取回的是原始 payload", detail["records"][0]["payload"]["o_id"], missing_key)
    check("证据指针没有丢失", detail["diff"]["evidence_keys"][0]["dedupe_key"], missing_key)


async def check_sku_master_flow() -> None:
    """§8.14：来源不覆盖本地、差异入队、确认生成版本、待匹配可重放。"""
    print("=== 6. SKU 主数据权威（真库）===")
    from app.modules.product import master as m

    async with SessionLocal() as s:
        result = await m.ingest_external_sku(
            s,
            system_type="JIANDAOYUN",
            external_code=f"{PREFIX}-SKU-{STAMP}",
            external_name=f"{PREFIX}件",
            fields={"unit": "箱"},
        )
        check("外部值不写回本地", result["applied_to_local"], False)
        sku = await s.get(Sku, IDS["sku"])
        check("本地单位没被改", sku.unit, "件")
        diffs, total = await m.list_sku_diffs(s, sku_id=IDS["sku"])
        check("进了待确认队列", total, 1)
        if not diffs:
            return
        check("差异类型是单位冲突", diffs[0]["diff_type"], "unit_conflict")
        overview = await m.sku_master_overview(s, IDS["sku"])
        unit = next(item for item in overview["fields"] if item["field_name"] == "unit")
        check("来源显示待核实", unit["source_status"], "待核实")
        check("权威归属未拍板", unit["authority_label"], "未拍板")

        # 确认"以本地为准" → 生成可回溯的版本（用户动作由路由/调用方提交）
        diff = await s.get(IntegrationDiff, diffs[0]["id"])
        outcome = await m.confirm_sku_diff(
            s, diff, resolution="keep_local", note="以本地口径为准", operator_id=IDS["owner"]
        )
        await s.commit()
    check("确认生成了版本", outcome["version"]["version_no"], 1)
    check("版本里记的是本地确认值", outcome["version"]["values"]["unit"], "件")
    async with SessionLocal() as s:
        version = await m.confirmed_master_version(s, IDS["sku"])
        check_true("正式报价可回溯这一版", version is not None and version["version_no"] == 1)


async def check_http_endpoints(admin_token: str, owner_token: str) -> None:
    """接口层：未接通如实显示；权限收口；差异与队列可读。"""
    print("=== 7. 接口层（HTTP）===")
    status, res = call("GET", "/integrations/erp/collection/status", owner_token)
    check("采集状态可读", status, 200)
    data = res.get("data") or {}
    check("采集能力如实标未验收", data.get("collection_capability", {}).get("verified"), False)
    check_true("列了未核实的来源", isinstance(data.get("unverified_sources"), list))

    status, res = call(
        "POST",
        "/integrations/erp/collection/run",
        owner_token,
        body={"object_type": "order", "shop_id": f"{PREFIX}-HTTP-{STAMP}"},
    )
    check("普通订单角色不能触发全公司采集（要 settings:manage）", status, 403)

    status, res = call(
        "POST",
        "/integrations/erp/collection/run",
        admin_token,
        body={"object_type": "order", "shop_id": f"{PREFIX}-HTTP-{STAMP}"},
    )
    check("管理员触发采集：未配置时明确失败", status, 422)
    check_true("错误里说清缺什么", "ERP" in str(res.get("message", "")), str(res.get("message"))[:120])

    status, res = call("GET", "/integrations/erp/reconcile/diffs?page_size=200", admin_token)
    check("差异清单可读", status, 200)

    status, res = call("GET", f"/sku-master/skus/{IDS['sku']}", owner_token)
    check("SKU 主数据总览可读", status, 200)
    fields = ((res.get("data") or {}).get("fields")) or []
    check_true("总览覆盖关键字段", len(fields) >= 10, f"{len(fields)} 个字段")
    check_true(
        "未核实的来源在接口上一律显示待核实",
        all(item["source_status"] in ("待核实", "已核实") for item in fields),
    )

    status, res = call(
        "GET", f"/sku-master/skus/{IDS['sku']}/confirmed", owner_token
    )
    check("已确认主数据可读", status, 200)
    check_true(
        "已确认版本可回溯（有版本号）",
        ((res.get("data") or {}).get("version") or {}).get("version_no") == 1,
        str(res.get("data"))[:120],
    )


async def main() -> int:
    admin_token = login("admin", "admin123")
    owner_token = login("zhangsan", "123456")
    print(f"已登录 admin / zhangsan（套件前缀 {PREFIX}，时间戳 {STAMP}）")
    await ensure_tables()
    await cleanup()
    await build_fixtures()
    try:
        await check_uniqueness_constraints()
        await check_repeat_collection_is_idempotent()
        await check_pagination_break_resumes()
        await check_concurrent_collect_uses_row_lock()
        await check_reconciliation_and_evidence()
        await check_sku_master_flow()
        await check_http_endpoints(admin_token, owner_token)
    finally:
        await cleanup()
    print()
    if FAILURES:
        print(f"FAILED {len(FAILURES)} 项：" + "、".join(FAILURES))
        return 1
    print("外部只读采集/对账/SKU 主数据 隔离回归 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
