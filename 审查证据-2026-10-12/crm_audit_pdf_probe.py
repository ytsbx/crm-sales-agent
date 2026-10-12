import asyncio
from decimal import Decimal as D
from crm_audit_probes import fixture, snapshot
from app.core.database import SessionLocal, engine
from app.core.deps import CurrentUser
from app.core.errors import AppError
from app.modules.quote import service as qs
from app.modules.quote.router import download_pdf

async def main():
    async with SessionLocal() as session:
        user, role, sku, customer, quote, version = await fixture(session)
        version.customer_name_snapshot=customer.name
        version.valid_until_snapshot=quote.valid_until
        session.add(await snapshot(session, role, sku, customer, version, D(100)))
        await session.flush()
        await qs.recalc_version(session, version)
        await qs.submit_for_approval(session, quote=quote, version=version, applicant_id=user.id, user_roles=['admin'])
        problems = await qs.master_confirmation_problems(session, version_id=version.id)
        try:
            await qs.ensure_freight_confirmed(session, version_id=version.id)
        except AppError as exc:
            print('PDF_FREIGHT_GATE', exc.code)
        response = await download_pdf(version.id, user=CurrentUser(user, permissions={'quote:view'}, roles=['admin'], data_scope='all'), session=session)
        print('PDF_BYPASS', {'approval_status': version.approval_status, 'unconfirmed_master':len(problems), 'pdf_status':response.status_code, 'pdf_bytes':len(response.body), 'freight':str(version.logistics_amount)})
        await session.rollback()
    await engine.dispose()
asyncio.run(main())
