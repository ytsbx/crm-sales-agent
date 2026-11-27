"""客户/联系人归属守卫的单元回归，不连数据库或外部服务。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.core.errors import AppError, ErrorCode
from app.modules.customer.model import Contact
from app.modules.opportunity import service
from app.modules.opportunity.model import Opportunity, OpportunityStage
from app.modules.user.model import User


def test_contact_cannot_belong_to_another_customer():
    session = SimpleNamespace(get=AsyncMock(return_value=Contact(id=9, customer_id=2, name='虚构联系人')))
    with pytest.raises(AppError, match='联系人不属于'):
        asyncio.run(service.validate_contact(session, customer_id=1, contact_id=9))


@pytest.mark.parametrize('contact', [None, SimpleNamespace(customer_id=1, deleted_at=object())])
def test_missing_or_deleted_contact_is_rejected(contact):
    session = SimpleNamespace(get=AsyncMock(return_value=contact))
    with pytest.raises(AppError, match='联系人不存在'):
        asyncio.run(service.validate_contact(session, customer_id=1, contact_id=9))


@pytest.mark.parametrize('customer_id, expected_contact_id', [(1, 9), (2, None)])
def test_clone_preserves_contact_only_for_same_customer(customer_id, expected_contact_id):
    async def scenario():
        source = Opportunity(id=7, customer_id=1, primary_contact_id=9, title='虚构采购',
                             owner_id=77, stage_id=5, status='win')
        session = SimpleNamespace(
            get=AsyncMock(side_effect=lambda model, key: (
                User(id=77, name='虚构负责人', status='active') if model is User
                else Contact(id=9, customer_id=1, name='虚构联系人')
            )),
            add=Mock(), flush=AsyncMock(),
        )
        user = SimpleNamespace(id=77)
        with patch('app.modules.customer.service.get_visible_customer', new_callable=AsyncMock) as customer_lookup, \
                patch.object(service, 'get_first_stage', new=AsyncMock(return_value=OpportunityStage(id=1))):
            clone = await service.clone_opportunity(session, source=source, user=user,
                                                    customer_id=customer_id, copy_items=False)
        customer_lookup.assert_awaited_once_with(session, user, customer_id)
        assert clone.customer_id == customer_id and clone.primary_contact_id == expected_contact_id
        assert clone.status == 'open' and clone.stage_id == 1
        # 复制默认归创建人（谁做谁负责），不再继承源商机负责人
        assert clone.owner_id == 77
        assert source.customer_id == 1 and source.primary_contact_id == 9 and source.status == 'win'
    asyncio.run(scenario())


@pytest.mark.parametrize('code, message, status', [
    (ErrorCode.DATA_SCOPE_DENIED, '无权访问客户', 403),
    (ErrorCode.NOT_FOUND, '客户不存在', 404),
])
def test_invisible_or_deleted_destination_never_creates_clone(code, message, status):
    async def scenario():
        source = Opportunity(id=7, customer_id=1, primary_contact_id=9, title='虚构采购')
        session = SimpleNamespace(add=Mock(), flush=AsyncMock())
        with patch('app.modules.customer.service.get_visible_customer',
                   new=AsyncMock(side_effect=AppError(code, message, status))):
            with pytest.raises(AppError, match=message):
                await service.clone_opportunity(session, source=source, user=SimpleNamespace(id=77),
                                                customer_id=2, copy_items=False)
        session.add.assert_not_called()
        session.flush.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.anyio
async def test_copy_items_empty_selection_and_quantity_only(monkeypatch):
    from decimal import Decimal

    from conftest import SyncSessionAsAsync, make_engine
    from sqlalchemy.orm import Session
    from app.modules.opportunity.model import OpportunityItem
    from app.modules.product import service as products

    monkeypatch.setattr(products, "ensure_skus_for_new_business", AsyncMock())
    engine = make_engine()
    OpportunityItem.__table__.create(engine)
    with Session(engine) as raw:
        source = OpportunityItem(opportunity_id=1, sku_id=1, quantity=3,
                                 target_price=100, currency="USD", specification="来源规格")
        target = OpportunityItem(opportunity_id=2, sku_id=1, quantity=1,
                                 target_price=12, currency="CNY", specification="原规格")
        raw.add_all([source, target])
        raw.flush()
        session = SyncSessionAsAsync(raw)

        empty = await service.copy_items_from(
            session, target_opportunity_id=2, source_opportunity_id=1, sku_ids=[]
        )
        assert empty == {"source_total": 0, "added": 0, "replaced": 0, "skipped": 0}
        assert raw.query(OpportunityItem).count() == 2
        replaced = await service.copy_items_from(
            session, target_opportunity_id=2, source_opportunity_id=1,
            sku_ids=[1], on_conflict="replace",
        )
        assert replaced["replaced"] == 1
        assert (target.quantity, target.target_price, target.currency, target.specification) == (
            Decimal(3), Decimal(12), "CNY", "原规格",
        )
        with pytest.raises(AppError, match="只支持人民币"):
            await service.copy_items_from(
                session, target_opportunity_id=2, source_opportunity_id=1,
                sku_ids=[1], on_conflict="add",
            )
        assert raw.query(OpportunityItem).count() == 2


@pytest.mark.anyio
async def test_disabled_product_blocks_new_items_without_removing_old_items(monkeypatch):
    from conftest import SyncSessionAsAsync, make_engine
    from sqlalchemy.orm import Session
    from app.modules.opportunity.model import OpportunityItem
    from app.modules.order import service as orders
    from app.modules.product.model import Product, Sku

    monkeypatch.setattr(orders, "ensure_currency_allowed", AsyncMock(return_value="CNY"))
    engine = make_engine()
    for model in (Product, Sku, OpportunityItem):
        model.__table__.create(engine)
    with Session(engine) as raw:
        raw.add(Product(id=1, name="停用产品", status="inactive"))
        raw.add(Sku(id=1, product_id=1, sku_code="R26", name="在用 SKU", status="active"))
        raw.add(OpportunityItem(opportunity_id=1, sku_id=1, quantity=1))
        raw.flush()
        class DeletingSession(SyncSessionAsAsync):
            async def delete(self, obj):
                raw.delete(obj)

        session = DeletingSession(raw)
        with pytest.raises(AppError, match="产品.*停用"):
            await orders.create_order(
                session, user_id=1, customer_id=1, items=[SimpleNamespace(sku_id=1)]
            )
        with pytest.raises(AppError, match="产品.*停用"):
            await service.replace_items(
                session, opportunity_id=2, items=[{"sku_id": 1, "quantity": 2}]
            )
        assert raw.query(OpportunityItem).count() == 1
        historical = await service.replace_items(
            session, opportunity_id=1, items=[{"sku_id": 1, "quantity": 2}]
        )
        assert len(historical) == 1 and historical[0].quantity == 2
