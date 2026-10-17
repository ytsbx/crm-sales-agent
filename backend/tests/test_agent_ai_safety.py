"""AI 边界：不可信参数、确认卡读取范围及失败信息隔离。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from tests.conftest import SyncSessionAsAsync, make_user
from app.core.errors import AppError
from app.core.config import settings
from app.modules.agent import runtime, commentary
from app.modules.agent.model import AgentAction
from app.modules.agent.tools import TOOLS, openai_tools, validate_tool_arguments
from app.modules.customer.model import Customer

@pytest.mark.parametrize('payload', [[], None, 1, {'customer_id': True}, {'customer_id': '1'}, {'extra': 1}, {}, {'customer_id': 1, 'extra': 2}])
def test_bad_model_arguments_rejected(payload):
    with pytest.raises(AppError):
        validate_tool_arguments(TOOLS['get_customer_overview'], payload)

def test_numbers_and_enums():
    for name,args in [('calculate_price',{'sku_id':1,'quantity':float('nan')}),('create_task',{'title':'test','priority':'invented'})]:
        with pytest.raises(AppError): validate_tool_arguments(TOOLS[name],args)
    assert validate_tool_arguments(TOOLS['create_task'],{'title':'test'}) == {'title':'test'}

def test_only_authorized_tools_sent_to_model():
    names={t['function']['name'] for t in openai_tools(make_user(permissions={'agent:use','customer:view'}))}
    assert 'search_customers' in names
    assert 'get_receivables_summary' not in names
    assert 'create_task' not in names

@pytest.mark.parametrize('owner,permissions,visible',[(101,{'customer:view'},True),(999,{'customer:view'},False),(101,set(),False),(None,{'customer:view'},True)])
def test_action_card_checks_scope_and_permission(db_session,owner,permissions,visible):
    customer=Customer(name='不可泄露的客户',level='A',status='active',pool_status='private',owner_id=owner)
    db_session.add(customer);db_session.flush()
    action=AgentAction(tool_name='create_followup',proposed_payload={'customer_id':customer.id,'content':'待确认'},status='awaiting_confirmation')
    result=asyncio.run(runtime.action_display(SyncSessionAsAsync(db_session),action,user=make_user(101,permissions=permissions)))
    assert ('不可泄露的客户' in str(result)) == visible

def test_provider_body_not_exposed():
    assert 'secret-token' not in runtime.model_error_message(RuntimeError('secret-token private-body'))

def test_bad_json_shape_becomes_recoverable_tool_result(monkeypatch):
    async def stream(calls=None,content=None):
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content,tool_calls=calls),finish_reason=None)])
    call=SimpleNamespace(index=0,id='call-1',function=SimpleNamespace(name='get_customer_overview',arguments='[]'))
    create=AsyncMock(side_effect=[stream([call]),stream(content='请提供有效的客户编号')])
    client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),close=AsyncMock())
    monkeypatch.setattr(runtime,'_client',lambda:client)
    monkeypatch.setattr(runtime,'_history',AsyncMock(return_value=[{'role':'user','content':'test'}]))
    monkeypatch.setattr(settings,'deepseek_api_key','fake')
    handler=AsyncMock();monkeypatch.setattr(TOOLS['get_customer_overview'],'handler',handler)
    session=SimpleNamespace(add=lambda row:None,commit=AsyncMock())
    row=SimpleNamespace(id=1,title='test',context_type=None,context_id=None)
    result=asyncio.run(runtime.run_turn(session,agent_session=row,user=make_user(permissions={'customer:view'}),text='test'))
    assert result['reply']=='请提供有效的客户编号'
    assert result['actions']==[]
    handler.assert_not_awaited()
    assert '工具参数必须是对象' in str(create.await_args.kwargs['messages'])
    client.close.assert_awaited_once()

def test_commentary_failure_hides_provider_body(monkeypatch):
    import openai
    class Client:
        def __init__(self,**kwargs):
            assert kwargs['max_retries']==0
            assert kwargs['timeout']<=20
            self.chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError('secret-token'))))
        async def close(self):pass
    monkeypatch.setattr(openai,'AsyncOpenAI',Client)
    monkeypatch.setattr(settings,'deepseek_api_key','fake')
    monkeypatch.setattr(settings,'agent_commentary_enabled',True)
    text,note=asyncio.run(commentary.build_commentary('risk-analysis',{'amount':1}))
    assert text is None and 'secret-token' not in note
    assert '本地统计结果不受影响' in note

@pytest.mark.parametrize('name,payload,permissions', [
    ('customer_summary', {'customer_id':1}, set()),
    ('opportunity_analysis', {'opportunity_id':1}, set()),
    ('product_recommendation', {'customer_id':1,'opportunity_id':None}, {'product:view'}),
    ('pricing_analysis', {'customer_id':None}, set()),
    ('quote_draft', {'customer_id':None}, {'opportunity:view'}),
    ('followup_suggestion', {'customer_id':None,'lead_id':1}, set()),
    ('risk_analysis', {}, set()),
])
def test_analysis_denies_missing_module_permission_before_query(name,payload,permissions):
    from app.modules.agent import router
    with pytest.raises(AppError) as exc:
        asyncio.run(getattr(router,name)(payload=SimpleNamespace(**payload),user=make_user(permissions=permissions),session=None))
    assert exc.value.http_status == 403


def test_opportunity_detail_does_not_read_other_modules_without_permission(db_session):
    from app.modules.agent.tools import get_opportunity_detail, ToolContext
    from app.modules.opportunity.model import Opportunity, OpportunityItem
    from app.modules.product.model import Sku
    for model in (Sku,OpportunityItem): model.__table__.create(db_session.get_bind(),checkfirst=True)
    customer=Customer(name='test',level='A',status='active',pool_status='private',owner_id=101)
    db_session.add(customer);db_session.flush()
    opp=Opportunity(customer_id=customer.id,title='test',owner_id=101,stage_id=1,status='open')
    db_session.add(opp);db_session.flush()
    ctx=ToolContext(SyncSessionAsAsync(db_session),make_user(101,permissions={'opportunity:view'}),1)
    # FollowUp 表未创建：如果缺权限时仍执行查询，本测试会直接失败。
    result=asyncio.run(get_opportunity_detail(ctx,opp.id))
    assert result['quotes']==[] and result['recent_followups']==[]
    assert len(result['unavailable_blocks'])==2
