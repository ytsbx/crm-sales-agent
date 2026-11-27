from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text, update
from sqlalchemy.schema import CreateTable
from starlette.requests import Request

from app.core.audit import AuditLog
from app.core.base import Base
from app.core.errors import AppError
from app.modules.customer import duplicates, router, tags, tags_router
from app.modules.customer.model import Contact, Customer, CustomerDuplicateCase, CustomerMergeLog, CustomerOwnerHistory, Tag, customer_tags
from app.modules.customer.schema import ContactBindCustomer, CustomerTransfer
from app.modules.customer.tags_schema import CustomerBatchTransfer, CustomerMergeRequest
from app.modules.pricing.model import CustomerPriceRule
from app.modules.product.model import Product, Sku
from app.modules.file.model import BusinessFile, FileRecord
from app.modules.public_pool import router as pool_router
from app.modules.public_pool.schema import PoolAssignRequest
from app.modules.user.model import User
from app.modules.wecom.model import WeComExternalContact
from conftest import SyncSessionAsAsync, follow_customer, make_user


@pytest.mark.anyio
async def test_batch_transfer_audit_records_actual_results(db_session, monkeypatch):
    AuditLog.__table__.create(db_session.get_bind(), checkfirst=True)
    db_session.add_all([
        Customer(id=41, name="需转交", owner_id=101),
        Customer(id=42, name="已是目标负责人", owner_id=102),
    ])
    db_session.flush()
    follow_customer(db_session, 41, 101)
    follow_customer(db_session, 42, 102)
    db_session.commit()
    transfer = AsyncMock(return_value={"moved_total": 2, "skipped_total": 1})
    monkeypatch.setattr(tags_router.svc, "transfer_customer", transfer)
    result = await tags_router.batch_transfer(
        CustomerBatchTransfer(customer_ids=[41, 41, 42, 43], owner_id=102),
        Request({"type": "http", "headers": []}), make_user(data_scope="all"),
        SyncSessionAsAsync(db_session),
    )
    audit = db_session.query(AuditLog).filter_by(action="batch_transfer").one().after_data
    assert audit["affected_customer_ids"] == [41]
    assert audit["already_assigned_customer_ids"] == [42]
    assert audit["unavailable_customer_ids"] == [43]
    assert {key: audit[key] for key in result["data"]} == result["data"]
    assert audit["documents_moved"] == 2 and audit["documents_skipped"] == 1
    transfer.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("permissions,can_view,can_resolve", [
    (("customer:view", "customer:update"), False, False),
    (("customer:view", "product:view"), True, False),
    (("customer:view", "product:view", "price:manage"), True, True),
])
async def test_merge_preview_applies_price_visibility_and_resolution_permissions(
    db_session, monkeypatch, permissions, can_view, can_resolve,
):
    for model in (Product, Sku, CustomerPriceRule):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    db_session.execute(CreateTable(CustomerDuplicateCase.__table__))
    for customer_id, price in [(41, 10), (42, 12)]:
        db_session.add(Customer(id=customer_id, name=f"客户 {customer_id}", owner_id=101))
        db_session.flush()
        follow_customer(db_session, customer_id, 101)
        db_session.add(CustomerPriceRule(customer_id=customer_id, sku_id=1, min_qty=1,
                                         agreed_price=price, effective_from=date(2026, 1, 1), status="active"))
    db_session.commit()
    for name in ("_count_links", "_count_special_links"):
        monkeypatch.setattr(tags_router.tag_svc, name, AsyncMock(return_value=[]))
    result = await tags_router.merge_preview(41, 42, make_user(101, permissions=permissions),
                                             SyncSessionAsAsync(db_session))
    conflict = result["data"]["conflicts"][0]
    assert result["data"]["blocking"] == ["customer_price"]
    assert conflict["count"] == 1
    assert bool(conflict["options"]) == can_resolve
    assert bool(conflict["items"]) == can_view
    if can_view:
        assert conflict["items"][0]["source_price"] == "10.0000"


@pytest.mark.anyio
@pytest.mark.parametrize("owner_status,third_pair", [
    ("active", None), ("inactive", None), (None, None),
    ("active", (41, 43)), ("active", (43, 42)),
])
async def test_customer_merge_closes_its_pair_and_checks_other_cases(db_session, owner_status, third_pair):
    tables = [tags._load_model(path, model).__table__ for path, model, _ in tags.MERGE_TARGETS]
    tables.extend(model.__table__ for model in (
        AuditLog, CustomerMergeLog, CustomerOwnerHistory, Tag, FileRecord, BusinessFile,
        WeComExternalContact, Product, Sku,
    ))
    Base.metadata.create_all(db_session.bind, tables=[*tables, customer_tags])
    db_session.execute(CreateTable(CustomerDuplicateCase.__table__))
    if owner_status is not None:
        db_session.add(User(id=202, name="原负责人", username="merge-owner", password_hash="x", status=owner_status))
    db_session.add_all([
        Customer(id=41, name="并入档案", owner_id=202),
        Customer(id=42, name="保留档案", pool_status="public"),
        Customer(id=43, name="第三家"),
        CustomerDuplicateCase(id=1, customer_id=42, candidate_id=41, status="pending", created_at=datetime.now(UTC)),
    ])
    if third_pair:
        db_session.add(CustomerDuplicateCase(id=2, customer_id=third_pair[0], candidate_id=third_pair[1],
                                            status="pending", created_at=datetime.now(UTC)))
    db_session.flush()
    follow_customer(db_session, 41, 202)
    db_session.commit()
    session = SyncSessionAsAsync(db_session)
    user = make_user(data_scope="all")
    payload = CustomerMergeRequest(source_customer_id=41, target_customer_id=42)
    request = Request({"type": "http", "headers": []})

    preview = await tags_router.merge_preview(41, 42, user, session)
    assert ("duplicate_case" in preview["data"]["blocking"]) == bool(third_pair)
    if third_pair:
        with pytest.raises(AppError, match="第三家客户"):
            await tags_router.merge_customers(payload, request, user, session)
        assert db_session.get(Customer, 41).deleted_at is None
        assert db_session.get(CustomerDuplicateCase, 1).status == "pending"
        assert db_session.query(CustomerMergeLog).count() == 0
        return

    await tags_router.merge_customers(payload, request, user, session)
    target = db_session.get(Customer, 42)
    # 客户没有默认负责人：合并只并跟进人，目标转私海
    assert target.owner_id is None
    assert target.pool_status == "private"
    pending, total = await duplicates.list_cases(session, user=user)
    assert pending == [] and total == 0
    resolved, total = await duplicates.list_cases(session, user=user, status="resolved")
    assert total == 1 and resolved[0]["decision_label"] == "客户档案已合并"
    assert db_session.query(CustomerOwnerHistory).count() == 0
    audit = db_session.query(AuditLog).filter_by(action="merge").one()
    assert audit.after_data["resolved_duplicate_case_ids"] == [1]
    assert audit.after_data["target_follower_ids"] == [202]


class CustomerLockProbe(SyncSessionAsAsync):
    def __init__(self, session, before_customer_lock):
        super().__init__(session)
        self.before_customer_lock = before_customer_lock

    async def execute(self, stmt, *args, **kwargs):
        if self.before_customer_lock is not None and "FOR UPDATE" in str(stmt):
            before_customer_lock, self.before_customer_lock = self.before_customer_lock, None
            before_customer_lock()
        return await super().execute(stmt, *args, **kwargs)


@pytest.mark.anyio
@pytest.mark.parametrize("route", [router.bind_contact_customer, router.change_contact_customer])
async def test_contact_move_rejects_changed_parent_before_taking_primary(db_session, route):
    from app.modules.followup.model import FollowUp

    AuditLog.__table__.create(db_session.bind)
    FollowUp.__table__.create(db_session.bind)
    db_session.execute(text("DROP INDEX uq_contacts_primary_per_customer"))
    db_session.execute(text(
        "CREATE UNIQUE INDEX uq_contacts_primary_per_customer ON contacts(customer_id) "
        "WHERE is_primary = 1 AND deleted_at IS NULL"
    ))
    db_session.add_all([
        Customer(id=41, name="原客户", owner_id=101),
        Customer(id=42, name="目标客户", owner_id=101),
        Customer(id=43, name="同事客户", owner_id=202),
        Contact(id=1, customer_id=41, name="待改挂"),
        Contact(id=2, customer_id=42, name="原主联系人", is_primary=True),
    ])
    db_session.flush()
    follow_customer(db_session, 41, 101)
    follow_customer(db_session, 42, 101)
    follow_customer(db_session, 43, 202)
    db_session.commit()

    def change_parent():
        db_session.execute(update(Contact).where(Contact.id == 1).values(customer_id=43),
                           execution_options={"synchronize_session": False})

    session = CustomerLockProbe(db_session, change_parent)
    request = Request({"type": "http", "headers": []})
    with pytest.raises(AppError, match="联系人已改挂") as exc_info:
        await route(1, ContactBindCustomer(customer_id=42, is_primary=True), request, make_user(), session)

    assert exc_info.value.http_status == 409
    db_session.expire_all()
    assert db_session.get(Contact, 1).customer_id == 43
    assert db_session.get(Contact, 2).is_primary
    assert db_session.query(AuditLog).count() == 0


@pytest.mark.anyio
async def test_customer_transfer_audits_only_documents_actually_reassigned(db_session):
    from app.modules.order.model import OrderDraft, SalesOrder
    from app.modules.quote.model import Quote
    from app.modules.sample.model import SampleRequest
    from app.modules.task.model import Task

    Base.metadata.create_all(db_session.bind, tables=[
        model.__table__ for model in (AuditLog, CustomerOwnerHistory, OrderDraft, SampleRequest, Task)
    ])
    db_session.add_all([
        Customer(id=41, name="客户", owner_id=101),
        User(id=102, name="接手同事", username="handover-owner", password_hash="x", status="active"),
        Quote(id=1, quote_no="Q-1", customer_id=41, owner_id=101),
        Quote(id=2, quote_no="Q-2", customer_id=41, owner_id=202),
        SalesOrder(id=3, order_no="O-3", customer_id=41, owner_id=101, sales_owner_id=101),
    ])
    db_session.flush()
    follow_customer(db_session, 41, 101)
    db_session.commit()
    result = await router.transfer_customer(
        41, CustomerTransfer(owner_id=102), Request({"type": "http", "headers": []}),
        make_user(), SyncSessionAsAsync(db_session),
    )

    audit = db_session.query(AuditLog).filter_by(action="transfer_documents").one()
    assert (audit.operator_id, audit.business_type, audit.business_id) == (101, "customer", 41)
    assert audit.before_data == {"owner_id": 101}
    assert audit.after_data["owner_id"] == 102
    assert audit.after_data["moved_document_ids"] == {"quote": [1], "order": [3]}
    assert audit.after_data["moved"] == result["data"]["document_transfer"]["moved"]
    assert "moved_document_ids" not in result["data"]["document_transfer"]
    assert db_session.get(Quote, 2).owner_id == 202
    assert db_session.get(SalesOrder, 3).sales_owner_id == 101


@pytest.mark.anyio
@pytest.mark.parametrize("editing", [False, True])
async def test_tag_name_conflict_preserves_outer_transaction(db_session, monkeypatch, editing):
    Tag.__table__.create(db_session.bind)
    original = Tag(name="原标签")
    db_session.add_all([original, Tag(name="重名标签")])
    db_session.commit()
    db_session.add(Customer(id=41, name="同事务客户"))
    session = SyncSessionAsAsync(db_session)
    monkeypatch.setattr(tags, "find_tag_by_name", AsyncMock(return_value=None))

    with pytest.raises(AppError, match="已存在") as exc_info:
        if editing:
            await tags.update_tag(session, original, {"name": "重名标签"})
        else:
            await tags.create_tag(session, name="重名标签")
    assert exc_info.value.http_status == 409
    db_session.commit()
    assert db_session.get(Customer, 41) is not None
    assert original.name == "原标签"


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["transfer", "assign", "release", "pool", "batch"])
async def test_transfer_checks_scope_on_locked_customer(db_session, monkeypatch, operation):
    db_session.add(Customer(id=41, name="客户", owner_id=101))
    db_session.flush()
    follow_customer(db_session, 41, 101)
    db_session.commit()

    def change_owner():
        from app.modules.customer.model import CustomerFollower

        db_session.execute(update(CustomerFollower).where(CustomerFollower.customer_id == 41)
                           .values(user_id=202),
                           execution_options={"synchronize_session": False})

    session = CustomerLockProbe(db_session, change_owner)
    request = Request({"type": "http", "headers": []})
    user = make_user(101, permissions=("customer:assign",))
    transfer = AsyncMock(side_effect=AssertionError("权限复核前不应进入写入服务"))
    monkeypatch.setattr(router.svc, "transfer_customer", transfer)

    with pytest.raises(AppError) as error:
        if operation == "transfer":
            await router.transfer_customer(41, CustomerTransfer(owner_id=303), request, user, session)
        elif operation == "assign":
            await router.assign_customer(41, CustomerTransfer(owner_id=303), request, user, session)
        elif operation == "release":
            await router.release_to_pool(41, request, None, user, session)
        elif operation == "pool":
            await pool_router.assign_public_customer(41, PoolAssignRequest(owner_id=303), request, user, session)
        else:
            await tags_router.batch_transfer(CustomerBatchTransfer(customer_ids=[41], owner_id=303), request, user, session)
    assert error.value.http_status == 403
    transfer.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["source_owner", "target_owner", "price_conflict"])
async def test_merge_checks_current_scope_and_price_permission(db_session, change):
    for model in (Product, Sku, CustomerPriceRule):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    db_session.add_all([Customer(id=i, name=f"客户 {i}", owner_id=101) for i in (41, 42)])
    db_session.flush()
    follow_customer(db_session, 41, 101)
    follow_customer(db_session, 42, 101)
    db_session.add(CustomerPriceRule(id=1, customer_id=41, sku_id=1, min_qty=1, agreed_price=10,
                                    effective_from=date(2026, 1, 1), status="active"))
    db_session.commit()

    def change_at_lock():
        if change == "price_conflict":
            db_session.add(CustomerPriceRule(id=2, customer_id=42, sku_id=1, min_qty=1, agreed_price=12,
                                            effective_from=date(2026, 1, 1), status="active"))
            db_session.flush()
        else:
            from app.modules.customer.model import CustomerFollower

            customer_id = 41 if change == "source_owner" else 42
            db_session.execute(update(CustomerFollower).where(CustomerFollower.customer_id == customer_id)
                               .values(user_id=202),
                               execution_options={"synchronize_session": False})

    session = CustomerLockProbe(db_session, change_at_lock)
    request = Request({"type": "http", "headers": []})
    user = make_user(101, permissions=("customer:update",))
    payload = CustomerMergeRequest(source_customer_id=41, target_customer_id=42,
                                   resolutions={"customer_price": "keep_target"})

    with pytest.raises(AppError) as error:
        await tags_router.merge_customers(payload, request, user, session)
    assert error.value.http_status == 403
    assert db_session.get(CustomerPriceRule, 1).status == "active"
    assert db_session.get(Customer, 41).deleted_at is None


@pytest.mark.anyio
async def test_deduplicate_existing_customer_uses_visibility_guard(monkeypatch):
    """查重编辑场景不能读取数据范围外客户的字段。"""

    class Session:
        def __init__(self):
            self.get_called = False

        async def get(self, *args, **kwargs):
            self.get_called = True
            raise AssertionError("deduplicate must use get_visible_customer")

    session = Session()
    user = object()

    async def deny(*args, **kwargs):
        raise AppError(40302, "该客户不在你的数据范围内", 403)

    monkeypatch.setattr(tags_router.svc, "get_visible_customer", deny)

    with pytest.raises(AppError) as exc_info:
        await tags_router.deduplicate({"customer_id": 99}, user=user, session=session)

    assert exc_info.value.http_status == 403
    assert session.get_called is False


@pytest.mark.anyio
async def test_deduplicate_existing_customer_keeps_request_mobile(monkeypatch):
    """现有客户查重使用客户名称，但手机号仍来自请求中的联系人字段。"""

    existing = SimpleNamespace(
        id=99,
        name="客户 99",
        tax_no=None,
        domain=None,
        address=None,
    )
    seen = {}

    async def visible(*args, **kwargs):
        return existing

    async def find(*args, **kwargs):
        seen.update(kwargs)
        return []

    async def scope(*args, **kwargs):
        return [1]

    monkeypatch.setattr(tags_router.svc, "get_visible_customer", visible)
    monkeypatch.setattr(tags_router, "find_duplicate_customers", find)
    monkeypatch.setattr(tags_router, "scoped_owner_ids", scope)

    await tags_router.deduplicate(
        {"customer_id": 99, "mobile": "13800000000"},
        user=object(),
        session=object(),
    )

    assert seen["company_name"] == "客户 99"
    assert seen["mobile"] == "13800000000"
@pytest.mark.anyio
async def test_exact_duplicate_candidates_are_not_crowded_out(db_session, monkeypatch):
    from app.modules.contact_util import find_duplicate_contacts, find_duplicate_customers
    from app.modules.customer.model import Contact, Customer
    from app.modules.settings import service as settings
    from tests.conftest import SyncSessionAsAsync, make_user

    monkeypatch.setattr(settings, "get_setting", AsyncMock(return_value=settings.DEFAULT_SETTINGS["dedup_scoring"]))
    db_session.add_all([
        Customer(id=i, name=f"同名科技{i}", owner_id=999 if i <= 30 else 101)
        for i in range(2, 65)
    ])
    db_session.add(Customer(id=65, name="不同名称", tax_no="EXACT-TAX", owner_id=101))
    db_session.flush()
    for i in range(31, 66):
        follow_customer(db_session, i, 101)
    db_session.add_all([Contact(id=i, name="张三", customer_id=i) for i in range(2, 65)])
    db_session.add(Contact(id=65, name="李四", customer_id=65, mobile="12345678901", email="same@example.test"))
    db_session.commit()
    session = SyncSessionAsAsync(db_session)
    customers = await find_duplicate_customers(
        session, company_name="同名科技", mobile=None, tax_no="EXACT-TAX", limit=5, owner_ids=[101],
    )
    assert 65 in {row["id"] for row in customers}

    contacts = await find_duplicate_contacts(
        session, user=make_user(), name="张三", email="same@example.test", limit=5,
    )
    assert contacts[0]["id"] == 65
