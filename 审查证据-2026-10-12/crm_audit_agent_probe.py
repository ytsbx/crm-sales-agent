import asyncio
from uuid import uuid4
from sqlalchemy import select, func
from crm_audit_probes import fixture
from app.core.database import SessionLocal, engine
from app.core.deps import CurrentUser
from app.core.errors import AppError
from app.modules.agent.model import AgentSession, AgentAction
from app.modules.agent.runtime import execute_action
from app.modules.opportunity.model import Opportunity, OpportunityItem, OpportunityStage
from app.modules.quote.model import Quote

async def main():
    async with SessionLocal() as session:
        user, role, sku, customer, initial_quote, version = await fixture(session)
        stage = (await session.execute(select(OpportunityStage).where(OpportunityStage.status=='active').order_by(OpportunityStage.id).limit(1))).scalar_one()
        opportunity = Opportunity(title='AUDIT_AGENT_' + uuid4().hex, customer_id=customer.id, owner_id=user.id, stage_id=stage.id)
        session.add(opportunity)
        await session.flush()
        session.add(OpportunityItem(opportunity_id=opportunity.id, sku_id=sku.id, quantity=1))
        # Normal scenario: SKU is disabled after demand was recorded.
        sku.status='inactive'
        agent_session = AgentSession(user_id=user.id, title='AUDIT partial transaction')
        session.add(agent_session)
        await session.flush()
        action = AgentAction(session_id=agent_session.id, action_type='create_quote_draft', tool_name='create_quote_draft', risk_level='L2', title='Create quote', proposed_payload={'opportunity_id': opportunity.id}, status='awaiting_confirmation')
        session.add(action)
        await session.commit()
        oid, aid = opportunity.id, action.id
        caller = CurrentUser(user, permissions={'quote:manage'}, roles=['admin'], data_scope='all')
        try:
            await execute_action(session, action=action, user=caller)
        except AppError as exc:
            print('AGENT_ERROR', exc.code, exc.message)
    async with SessionLocal() as verify:
        quotes = list((await verify.execute(select(Quote).where(Quote.opportunity_id==oid))).scalars())
        action = await verify.get(AgentAction, aid)
        print('AGENT_PARTIAL_COMMIT', {'action_status': action.status, 'persisted_quotes':len(quotes), 'quote_has_version': bool(quotes and quotes[0].current_version_id)})
    await engine.dispose()
asyncio.run(main())
