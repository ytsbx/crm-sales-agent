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
        assert source.customer_id == 1 and source.primary_contact_id == 9 and source.status == 'win'
        if expected_contact_id is None:
            session.get.assert_awaited_once_with(User, 77)
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
