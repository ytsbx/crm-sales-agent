"""AI确认卡使用报价精确金额，同时保留既有权限和数据范围过滤。"""
import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.conftest import SyncSessionAsAsync, make_user
from app.modules.agent import runtime
from app.modules.agent.model import AgentAction
from app.modules.customer.model import Customer
from app.modules.quote.model import Quote, QuoteVersion


@pytest.mark.parametrize('amount', ['99999999999999.99', '1234.56', '0.01', '0.00'])
@pytest.mark.parametrize('case', ['visible', 'outside_scope', 'no_permission', 'deleted'])
def test_quote_approval_card_exact_amount_and_visibility(db_session, amount, case):
    db_session.add(Customer(id=1, name='隔离确认卡客户', owner_id=101))
    quote=Quote(id=1,quote_no='ISO-CARD-1',customer_id=1,owner_id=999 if case=='outside_scope' else 101,
                deleted_at=datetime.now(UTC) if case=='deleted' else None)
    version=QuoteVersion(id=1,quote_id=1,version_no=1,total_amount=Decimal(amount),currency='CNY',created_at=datetime.now(UTC))
    db_session.add_all([quote,version]);db_session.flush()
    action=AgentAction(tool_name='request_quote_approval',proposed_payload={'quote_version_id':1,'reason':'隔离卡片验证'},status='approval_required')
    user=make_user(101,permissions=set() if case=='no_permission' else {'quote:view'})
    result=asyncio.run(runtime.action_display(SyncSessionAsAsync(db_session),action,user=user))
    fields={row['label']:row['value'] for row in result['fields']}
    assert fields['申请原因']=='隔离卡片验证'
    assert result['can_confirm'] is True
    if case=='visible':
        assert fields['报价金额']==f'¥{Decimal(amount):,.2f}'
        assert fields['报价单']=='ISO-CARD-1 第 1 版'
    else:
        assert '报价金额' not in fields and '报价单' not in fields
