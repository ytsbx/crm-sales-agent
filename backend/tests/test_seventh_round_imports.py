"""第七批导入完整性：7.1 事务隔离、7.2 数值校验、7.4 成本、7.5 历史客户、7.6 预览。

## 为什么要这一套

第七批的问题全是"看着导入成功、数据其实是错的"这一类，接口冒烟测不出来：

- 7.1 「好行、坏行、好行」最后只剩第一行成功（session 被坏行带进失败态）；
- 7.2 负指导价、倒置区间/有效期、NaN、非整数 MOQ 一律照收；
- 7.4 四项成本全空的模板行会造出一条"全零成本"，核价据此算出假毛利；
- 7.5 非法/未来日期静默变空、负责人写错静默归到导入人名下；
- 7.6 产品导入没有 preview 参数（通用导入组件发 preview=true 时**真的写库了**）。

用**真函数 + 内存 SQLite**（见 tests/conftest.py）：把 session 换成 mock
等于自己重写一遍 SQL，测不出"过滤条件写错列"这类错；直接调路由函数也比走
HTTP 更贴近缺陷复现（审查方就是这么复现的）。
"""

import asyncio
import csv as csv_module
import io
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from tests.conftest import SyncSessionAsAsync, _register_sqlite_bigint_as_integer

from app.core.errors import AppError
from app.core.importing import (
    ImportReport,
    RowErrors,
    diff_preview,
    make_preview_token,
    read_preview_token,
    row_savepoint,
)
from app.modules.customer.model import Customer
from app.modules.pricing.model import ProductCost
from app.modules.product.io import sku_fields_from_row
from app.modules.product.model import Product, Sku
from app.modules.user.model import User


# --------------------------------------------------------------------- 会话替身


class _NestedTxn:
    """`session.begin_nested()` 的异步门面：替身会话背后是同步 Session。"""

    def __init__(self, nested) -> None:
        self._nested = nested

    async def commit(self) -> None:
        self._nested.commit()

    async def rollback(self) -> None:
        self._nested.rollback()


class SavepointSession(SyncSessionAsAsync):
    """在共享替身上补齐本套用例真正用到的三件事：SAVEPOINT、回滚、pending 清理。

    为什么单独一个子类而不是改 conftest：conftest 是别的用例共用的替身，
    这里要的是 7.1 的保存点语义，混进去会让"替身故意不实现某方法"的护栏失效。
    """

    async def begin_nested(self) -> _NestedTxn:
        return _NestedTxn(self._session.begin_nested())

    async def rollback(self) -> None:
        self._session.rollback()

    @property
    def new(self):
        return list(self._session.new)

    def expunge(self, obj) -> None:
        self._session.expunge(obj)


class DB:
    """一个内存库的两副面孔：`session` 给被测异步代码，`raw` 给用例自己断言。

    为什么不让替身会话同时当同步用：`execute` 一旦是协程，用例里
    `db.execute(...).scalars()` 只会拿到 coroutine（伴随"never awaited"警告），
    断言永远为假 —— "测试通过但其实没查"这种坑比直接报错难发现得多。
    """

    def __init__(self, raw: Session) -> None:
        self.raw = raw
        self.session = SavepointSession(raw)

    def scalars_all(self, stmt):
        return self.raw.execute(stmt).scalars().all()

    def scalars_one(self, stmt):
        return self.raw.execute(stmt).scalars().one()


@pytest.fixture()
def db():
    """一个全新的内存库（支持 SAVEPOINT）。"""
    from sqlalchemy import create_engine

    from app.core.audit import AuditLog
    from app.modules.customer.model import CustomerDuplicateCase
    from app.modules.settings.model import SystemSetting

    _register_sqlite_bigint_as_integer()
    engine = create_engine("sqlite+pysqlite:///:memory:")

    # pysqlite 驱动自己管事务，不关掉它 SQLAlchemy 的 SAVEPOINT 会失效
    # （驱动会隐式 BEGIN，嵌套保存点报 "cannot start a transaction within a transaction"）。
    # 这是 SQLAlchemy 官方文档给的 pysqlite 配方。
    # 顺带补两个 PostgreSQL 才有的函数：客户重复候选表的唯一索引用了
    # LEAST/GREATEST，SQLite 建表时会报 "no such function"。
    @event.listens_for(engine, "connect")
    def _prepare_sqlite_connection(dbapi_connection, _record):  # noqa: ARG001
        dbapi_connection.isolation_level = None
        # deterministic=True 是必须的：SQLite 不允许在索引表达式里用非确定性函数
        dbapi_connection.create_function(
            "LEAST", 2, lambda a, b: min(a, b), deterministic=True
        )
        dbapi_connection.create_function(
            "GREATEST", 2, lambda a, b: max(a, b), deterministic=True
        )

    @event.listens_for(engine, "begin")
    def _explicit_begin(conn):
        conn.exec_driver_sql("BEGIN")

    tables = [
        model.__table__
        for model in (
            User,
            Product,
            Sku,
            ProductCost,
            Customer,
            CustomerDuplicateCase,
            SystemSetting,
            AuditLog,
        )
    ]
    from app.core.base import Base

    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as session:
        yield DB(session)
    engine.dispose()


def upload(content: str, name: str = "import.csv") -> UploadFile:
    data = content.encode("utf-8-sig")
    return UploadFile(file=io.BytesIO(data), filename=name, size=len(data))


class FakeRequest:
    """路由只用到 request.headers / request.client（写审计取 IP），给个最小替身。"""

    headers: dict = {}
    client = None


class FakeUser:
    id = 1
    name = "测试管理员"
    roles = ["admin"]
    data_scope = "all"
    department_id = None

    def has(self, code: str) -> bool:  # noqa: ARG002
        return True


def seed_basics(db: DB) -> None:
    db.raw.add(
        User(id=1, name="测试管理员", username="tester", password_hash="x", status="active")
    )
    db.raw.add(Product(id=1, name="纸箱", status="active"))
    db.raw.add(Sku(id=1, product_id=1, sku_code="SKU-1", status="active", unit="件"))
    db.raw.commit()


# ------------------------------------------------------- 7.1 逐行事务（SAVEPOINT）


def test_row_savepoint_keeps_session_usable_after_a_bad_row(db):
    """坏行之后，后面的好行必须还能写进去（原来会一起失败）。"""

    async def _run():
        report = ImportReport("probe", 3)
        for index, code in enumerate(["好行一", "好行二"], start=2):
            try:
                async with row_savepoint(db.session):
                    db.raw.add(Product(name=code, status="active"))
                    await db.session.flush()
                report.created_row(index, code)
            except Exception as exc:  # noqa: BLE001
                report.failed_row(index, code, str(exc)[:80])

        # 中间插一行必然违规的（name 非空约束）
        try:
            async with row_savepoint(db.session):
                db.raw.add(Product(name=None, status="active"))
                await db.session.flush()
        except Exception as exc:  # noqa: BLE001
            report.failed_row(3, "坏行", str(exc)[:80])

        # 坏行之后再写一行好的：原来这里会拿 PendingRollbackError
        try:
            async with row_savepoint(db.session):
                db.raw.add(Product(name="坏行之后的好行", status="active"))
                await db.session.flush()
            report.created_row(4, "坏行之后的好行")
        except Exception as exc:  # noqa: BLE001
            report.failed_row(4, "坏行之后的好行", str(exc)[:80])
        db.raw.commit()
        return report

    report = asyncio.run(_run())
    names = set(db.scalars_all(select(Product.name)))
    assert report.failed_count == 1
    assert {"好行一", "好行二", "坏行之后的好行"} <= names


def test_row_savepoint_drops_pending_objects_of_failed_row(db):
    """报错行里 add 过但没 flush 的对象，不能留到最后的 commit 里偷偷写进去。"""

    async def _run():
        try:
            async with row_savepoint(db.session):
                db.raw.add(Product(name="幽灵产品", status="active"))
                raise RuntimeError("这一行业务失败")
        except RuntimeError:
            pass
        db.raw.commit()

    asyncio.run(_run())
    names = db.scalars_all(select(Product.name))
    assert "幽灵产品" not in names


def test_row_errors_counts_one_failed_row_with_all_reasons():
    """同一行的多个问题算**一行失败**，但原因一条都不能少（7.6 统计口径）。"""
    report = ImportReport("probe", 1)
    errs = RowErrors(2, "SKU-1")
    errs.add("数量下限不能大于数量上限")
    errs.add("生效起始日不能晚于生效截止日")
    report.failed_row(2, "SKU-1", errs.reasons)
    report.failed_row(2, "SKU-1", "与现有规则重叠")
    assert report.failed_count == 1
    assert len(report.failed) == 1
    assert report.failed[0]["reason"].count("；") == 2


# ------------------------------------------------------------ 7.2 数值与区间校验


def test_nan_and_infinity_are_rejected():
    """`Decimal("NaN")` 是合法 Decimal —— 不显式挡掉就会一路进库/进比较。"""
    errs = RowErrors(2, "SKU-1")
    assert errs.decimal("NaN", "指导价") is None
    assert errs.decimal("Infinity", "指导价") is None
    assert len(errs) == 2


def test_negative_and_margin_range_are_rejected():
    errs = RowErrors(2, "SKU-1")
    assert errs.decimal("-5", "指导价", non_negative=True) is None
    assert "不能为负数" in errs.reasons[0]

    errs2 = RowErrors(3, "SKU-1")
    assert errs2.decimal("30", "目标利润率", positive=True, maximum=1) is None
    assert "不能大于 1" in errs2.reasons[0]

    errs3 = RowErrors(4, "SKU-1")
    assert errs3.decimal("0.30", "目标利润率", positive=True, maximum=1) is not None


def test_sku_int_columns_refuse_truncation():
    """MOQ 2.9 **不能**被截成 2（原来 int(float(x)) 就是这么干的）。"""
    with pytest.raises(ValueError, match="不是合法整数"):
        sku_fields_from_row({"SKU编码": "SKU-1", "MOQ": "2.9"})
    assert sku_fields_from_row({"SKU编码": "SKU-1", "MOQ": "3"})["moq"] == 3


def test_sku_numeric_columns_reject_nan_and_negative():
    with pytest.raises(ValueError):
        sku_fields_from_row({"SKU编码": "SKU-1", "重量": "NaN"})
    with pytest.raises(ValueError, match="不能为负数"):
        sku_fields_from_row({"SKU编码": "SKU-1", "装箱数": "-1"})


def test_sku_text_length_is_reported_in_plain_language():
    with pytest.raises(ValueError, match="SKU编码长度不能超过 64"):
        sku_fields_from_row({"SKU编码": "X" * 65})


def test_date_fields_report_format_and_future():
    errs = RowErrors(2, "SKU-1")
    assert errs.date_value("2026/13/45", "生效起始日") is None
    assert "格式应为 YYYY-MM-DD" in errs.reasons[0]

    errs2 = RowErrors(3, "SKU-1")
    future = (datetime.now(UTC) + timedelta(days=5)).date().isoformat()
    assert errs2.date_value(future, "最后联系日期", allow_future=False) is None
    assert "未来日期" in errs2.reasons[0]


def test_date_accepts_slash_and_dot_separators():
    errs = RowErrors(2, "SKU-1")
    assert errs.date_value("2026.10.01", "生效起始日") == date(2026, 10, 1)
    assert errs.date_value("2026/10/01", "生效起始日") == date(2026, 10, 1)
    assert not errs


# --------------------------------------------------------------- 7.4 成本口径


def test_cost_model_distinguishes_missing_from_explicit_zero():
    """NULL = 未提供，0 = 明确为零；合计把 NULL 当 0，但要能说出缺哪几项。"""
    cost = ProductCost(
        sku_id=1,
        purchase_cost=None,
        production_cost=0,
        package_cost=None,
        processing_cost=0,
        effective_from=date(2026, 10, 1),
    )
    assert cost.total_cost == 0
    assert cost.is_complete is False
    assert cost.missing_components == ["采购成本", "包装成本"]

    full = ProductCost(
        sku_id=1,
        purchase_cost=0,
        production_cost=0,
        package_cost=0,
        processing_cost=0,
        effective_from=date(2026, 10, 1),
    )
    # 四项都显式填 0 = 已知的零成本（客户供料这类真实场景），合法
    assert full.is_complete is True
    assert full.missing_components == []


COST_HEADER = (
    "SKU编码,采购成本,生产成本,包装成本,加工成本,币种(默认CNY),"
    "生效起始日(YYYY-MM-DD),生效截止日(YYYY-MM-DD),备注\n"
)


def test_cost_import_rejects_all_blank_and_non_cny(db):
    """空白成本不能造出全零成本行；外币成本本轮明确拒绝。"""
    from app.modules.pricing import io_router as pricing_io

    seed_basics(db)
    csv_text = COST_HEADER + (
        "SKU-1,,,,,CNY,2026-10-01,,四项全空\n"
        "SKU-1,10,,, ,USD,2026-10-01,,外币成本\n"
    )
    result = asyncio.run(
        pricing_io.import_costs(
            request=FakeRequest(),
            file=upload(csv_text),
            preview=False,
            preview_token=None,
            user=FakeUser(),
            session=db.session,
        )
    )
    costs = db.scalars_all(select(ProductCost))
    assert costs == []
    assert result["data"]["failed_count"] == 2
    reasons = " ".join(item["reason"] for item in result["data"]["failed"])
    assert "全为空" in reasons
    assert "只支持人民币" in reasons


def test_cost_import_keeps_partial_blank_as_null(db):
    """只填部分 → 未填的列存 NULL，核价才能提示「成本不完整」。"""
    from app.modules.pricing import io_router as pricing_io

    seed_basics(db)
    csv_text = COST_HEADER + "SKU-1,12.5,,,,CNY,2026-10-01,,只填采购\n"
    result = asyncio.run(
        pricing_io.import_costs(
            request=FakeRequest(),
            file=upload(csv_text),
            preview=False,
            preview_token=None,
            user=FakeUser(),
            session=db.session,
        )
    )
    cost = db.scalars_one(select(ProductCost))
    assert result["data"]["created_count"] == 1
    assert cost.purchase_cost == pytest.approx(12.5)
    assert cost.production_cost is None
    assert cost.is_complete is False


def test_cost_import_updates_same_effective_day_and_keeps_blank(db):
    """同日再导 = 更新那一版；空白 = 保留旧值（不是改成 0）。"""
    from app.modules.pricing import io_router as pricing_io

    seed_basics(db)
    first = COST_HEADER + "SKU-1,10,2,,,CNY,2026-10-01,,首发\n"
    asyncio.run(
        pricing_io.import_costs(
            request=FakeRequest(), file=upload(first), preview=False,
            preview_token=None, user=FakeUser(), session=db.session,
        )
    )
    second = COST_HEADER + "SKU-1,12,,,,CNY,2026-10-01,,改采购价\n"
    result = asyncio.run(
        pricing_io.import_costs(
            request=FakeRequest(), file=upload(second), preview=False,
            preview_token=None, user=FakeUser(), session=db.session,
        )
    )
    cost = db.scalars_one(select(ProductCost))
    assert result["data"]["updated_count"] == 1
    assert cost.purchase_cost == pytest.approx(12)
    # 第二行没写生产成本 → 保留旧值 2，不能被当成 0
    assert cost.production_cost == pytest.approx(2)


def test_cost_preview_writes_nothing_but_plans_the_row(db):
    from app.modules.pricing import io_router as pricing_io

    seed_basics(db)
    csv_text = COST_HEADER + "SKU-1,12.5,,,,CNY,2026-10-01,,预览\n"
    result = asyncio.run(
        pricing_io.import_costs(
            request=FakeRequest(), file=upload(csv_text), preview=True,
            preview_token=None, user=FakeUser(), session=db.session,
        )
    )
    assert db.scalars_all(select(ProductCost)) == []
    assert result["data"]["preview"] is True
    assert result["data"]["created_count"] == 1
    assert result["data"]["preview_token"]


def test_price_rule_import_rejects_bad_numbers_and_deleted_sku(db):
    """7.2 的价格规则侧：负数、倒置区间/有效期逐行报错且不落库。"""
    from app.modules.pricing import io_router as pricing_io

    seed_basics(db)
    csv_text = (
        "SKU编码,客户等级(留空=通用),数量下限,数量上限(留空=不限),标准价,指导价,"
        "最低保护价,目标利润率(如0.30),生效起始日(YYYY-MM-DD),"
        "生效截止日(YYYY-MM-DD),历史标记(填1=历史资料),备注\n"
        # 负指导价 + 倒置区间 + 倒置有效期
        "SKU-1,A,100,50,95,-85,70,0.30,2026-12-31,2026-10-01,,三处都错\n"
        # MOQ 之外：不存在的 SKU
        "SKU-404,,,,,100,,,,,,\n"
    )
    result = asyncio.run(
        pricing_io.import_price_rules(
            request=FakeRequest(), file=upload(csv_text), preview=False,
            preview_token=None, user=FakeUser(), session=db.session,
        )
    )
    assert result["data"]["created_count"] == 0
    assert result["data"]["failed_count"] == 2
    first_reason = result["data"]["failed"][0]["reason"]
    assert "不能为负数" in first_reason
    assert "不能大于数量上限" in first_reason
    assert "不能晚于生效截止日" in first_reason


# ------------------------------------------------------- 7.5 历史客户导入口径


def test_customer_template_row_matches_header_count():
    """模板示例行必须和表头一一对齐（原来 9 列表头只有 8 个值，备注落进日期列）。"""
    from app.modules.customer import io as customer_io
    from app.modules.customer.io_router import import_template

    response = asyncio.run(import_template(_=FakeUser()))
    lines = [line for line in response.body.decode("utf-8-sig").splitlines() if line.strip()]
    assert len(lines) == 2
    header = next(csv_module.reader([lines[0]]))
    sample = next(csv_module.reader([lines[1]]))
    assert len(sample) == len(header) == len(customer_io.TEMPLATE_HEADERS)


CUSTOMER_HEADER = "客户名称,负责人登录名,最后联系日期\n"


def test_customer_import_unknown_owner_fails_instead_of_taking_importer(db):
    from app.modules.customer import io_router as customer_io_router

    seed_basics(db)
    result = asyncio.run(
        customer_io_router.import_customers(
            request=FakeRequest(),
            file=upload(CUSTOMER_HEADER + "某客户,nobody,2025-01-01\n"),
            preview=False,
            preview_token=None,
            user=FakeUser(),
            session=db.session,
        )
    )
    assert db.scalars_all(select(Customer)) == []
    assert result["data"]["failed_count"] == 1
    assert "找不到登录名" in result["data"]["failed"][0]["reason"]


def test_customer_import_marks_unknown_contact_time(db):
    """没给联系日期 → 标 last_contact_unknown；填了 → 按真实时间写、不标未知。"""
    from app.modules.customer import io_router as customer_io_router

    seed_basics(db)
    result = asyncio.run(
        customer_io_router.import_customers(
            request=FakeRequest(),
            file=upload(CUSTOMER_HEADER + "老客户A,tester,\n老客户B,tester,2025-03-18\n"),
            preview=False,
            preview_token=None,
            user=FakeUser(),
            session=db.session,
        )
    )
    rows = {row.name: row for row in db.scalars_all(select(Customer))}
    assert result["data"]["created_count"] == 2
    assert rows["老客户A"].last_contact_unknown is True
    assert rows["老客户A"].last_followup_at is None
    assert rows["老客户B"].last_contact_unknown is False
    assert rows["老客户B"].last_followup_at is not None
    assert result["data"]["unknown_contact_count"] == 1


def test_customer_import_rejects_invalid_and_future_dates(db):
    from app.modules.customer import io_router as customer_io_router

    seed_basics(db)
    future = (datetime.now(UTC) + timedelta(days=10)).date().isoformat()
    result = asyncio.run(
        customer_io_router.import_customers(
            request=FakeRequest(),
            file=upload(
                CUSTOMER_HEADER + f"未来客户,tester,{future}\n乱填客户,tester,去年三月\n"
            ),
            preview=False,
            preview_token=None,
            user=FakeUser(),
            session=db.session,
        )
    )
    assert result["data"]["failed_count"] == 2
    reasons = " ".join(item["reason"] for item in result["data"]["failed"])
    assert "未来日期" in reasons
    assert "格式应为" in reasons


def test_customer_import_preview_lists_owner_mapping_and_writes_nothing(db):
    """预览要能核对负责人映射（哪个登录名落到谁），且不写库。"""
    from app.modules.customer import io_router as customer_io_router

    seed_basics(db)
    result = asyncio.run(
        customer_io_router.import_customers(
            request=FakeRequest(),
            file=upload(CUSTOMER_HEADER + "预览客户,tester,2025-01-01\n"),
            preview=True,
            preview_token=None,
            user=FakeUser(),
            session=db.session,
        )
    )
    assert db.scalars_all(select(Customer)) == []
    mapping = result["data"]["owner_mapping"]
    assert mapping and mapping[0]["login"] == "tester"
    assert mapping[0]["resolved"] == "username"
    assert result["data"]["preview_token"]


# ------------------------------------------------------------ 7.6 预览快照比对


def test_preview_token_detects_changed_rows():
    plan = {"2": "created", "3": "created"}
    token = make_preview_token("product", "sha-of-file", plan)
    file_sha, planned = read_preview_token(token, "product")
    assert file_sha == "sha-of-file"

    diff = diff_preview(
        planned,
        {"2": "created", "3": "failed"},
        file_sha256="sha-of-file",
        planned_file=file_sha,
    )
    assert diff["file_changed"] is False
    assert [item["row"] for item in diff["changed_rows"]] == [3]
    assert diff["changed_rows"][0]["after_label"] == "失败"


def test_preview_token_is_rejected_for_another_module_or_tampering():
    token = make_preview_token("product", "sha", {"2": "created"})
    with pytest.raises(AppError):
        read_preview_token(token, "sku")
    with pytest.raises(AppError):
        read_preview_token(token[:-2] + "xx", "product")


def test_import_report_truncates_loudly(monkeypatch):
    """超过上限要**显式**说明被截断，不能让人以为拿到的是全部。"""
    from app.core import importing

    monkeypatch.setattr(importing.settings, "import_max_error_rows", 2)
    report = ImportReport("probe", 5)
    for row in range(2, 6):
        report.failed_row(row, f"SKU-{row}", "随便一个原因")
    body = report.payload(message="x")["data"]
    assert body["failed_count"] == 4
    assert body["failed_total"] == 4
    assert body["failed_truncated"] is True
    assert len(body["failed"]) == 2


def test_product_import_preview_does_not_write(db):
    """product 导入原来**没有** preview 参数：通用导入组件发 preview=true 时
    它照样写库（FastAPI 忽略未知表单字段）。现在必须真的不写。"""
    from app.modules.product import io_router as product_io

    seed_basics(db)
    result = asyncio.run(
        product_io.import_products(
            request=FakeRequest(),
            file=upload("产品名称,产品线\n预览产品,包装\n"),
            preview=True,
            preview_token=None,
            user=FakeUser(),
            session=db.session,
        )
    )
    names = db.scalars_all(select(Product.name))
    assert "预览产品" not in names
    assert result["data"]["preview"] is True


def test_product_import_continues_after_a_bad_row(db):
    """7.1 的产品侧：「好行、坏行、好行」必须两行成功一行失败。"""
    from app.modules.product import io_router as product_io

    seed_basics(db)
    csv_text = "产品名称,产品线\n好产品一,包装\n" + "X" * 300 + ",包装\n好产品二,包装\n"
    result = asyncio.run(
        product_io.import_products(
            request=FakeRequest(),
            file=upload(csv_text),
            preview=False,
            preview_token=None,
            user=FakeUser(),
            session=db.session,
        )
    )
    names = set(db.scalars_all(select(Product.name)))
    assert result["data"]["created_count"] == 2
    assert result["data"]["failed_count"] == 1
    assert {"好产品一", "好产品二"} <= names
