"""仅隔离本机库：草稿隔离、确认闸门、来源明细、幂等和版本文件。"""
import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlparse
from uuid import uuid4
from sqlalchemy import String, delete, select, func
from _test_support import require_isolated_db

require_isolated_db()
from app.core.audit import AuditLog
from app.core.config import settings
from app.core.database import SessionLocal
from app.modules.bizdoc.model import BizDoc
from app.modules.customer.model import Customer
from app.modules.followup.model import FollowUp
from app.modules.inquiry.model import CustomInquiry
from app.modules.notification.model import BusinessEvent, Notification
from app.modules.opportunity.model import Opportunity, OpportunityStage, OpportunityStageHistory
from app.modules.order.model import OrderDraft, OrderDraftItem, SalesOrder, SalesOrderItem, OrderStatusHistory
from app.modules.payment.model import ReceivablePlan
from app.modules.quote.model import Quote, QuoteVersion, QuoteItem, QuoteSendLog, QuoteCharge
from app.modules.user.model import User
from scripts.check_review_regressions import BASE, call, login


async def main():
    import app.main
    _ = app.main
    assert urlparse(BASE).hostname in {'localhost','127.0.0.1','::1'}
    db=urlparse(settings.database_url)
    assert db.hostname in {'localhost','127.0.0.1','::1'} and ('test' in db.path.lower() or os.getenv('CI')=='true')
    assert settings.wecom_push_off and settings.dingtalk_push_off and not settings.scheduler_enabled and not settings.wecom_transfer_enabled
    marker='CHKOD'+uuid4().hex[:8]; cids=[]; qids=[]; oids=[]
    admin=login('admin','admin123'); salesman=login('zhangsan','123456')
    def req(method,path,body=None,expected=200,token=None):
        code,result=call(method,path,body=body,token=token or admin)
        assert code==expected,(method,path,code,result)
        return result.get('data')
    try:
        async with SessionLocal() as s:
            uid=(await s.execute(select(User.id).where(User.username=='admin'))).scalar_one()
            stage=(await s.execute(select(OpportunityStage.id).order_by(OpportunityStage.sequence))).scalars().first()
            customer=Customer(name=marker,owner_id=uid); s.add(customer); await s.flush(); cids.append(customer.id)
            opp=Opportunity(customer_id=customer.id,owner_id=uid,title=marker,stage_id=stage,status='open')
            s.add(opp); await s.flush(); oids.append(opp.id)
            inquiry=CustomInquiry(customer_id=customer.id,opportunity_id=opp.id,title=marker,inquiry_no=marker,description='规格',quantity=10000,created_by=uid)
            s.add(inquiry); await s.flush(); iid=inquiry.id
            quote=Quote(quote_no=marker,customer_id=customer.id,opportunity_id=opp.id,owner_id=uid,status='draft',valid_until=(datetime.now(UTC)+timedelta(days=7)).date())
            s.add(quote); await s.flush(); qids.append(quote.id); qid=quote.id
            version=QuoteVersion(quote_id=qid,version_no=1,approval_status='not_submitted',created_at=datetime.now(UTC),total_amount=300)
            s.add(version); await s.flush(); vid=version.id; quote.current_version_id=vid
            # 运费分离（2026-10-09）：正式发送前必须已确认运费。本套件验的是订单草稿，
            # 不是运费 —— 补一条**已确认**的运费让流程能走到它要验的那一步。
            s.add(QuoteCharge(quote_version_id=vid,charge_type='logistics',description='夹具运费',
                              amount=Decimal('20'),logistics_confirmed_at=datetime.now(UTC)))
            line_ids=[]
            for qty in (10,20):
                line=QuoteItem(quote_version_id=vid,inquiry_id=iid,inquiry_no_snapshot=marker,sku_name_snapshot=marker,spec_snapshot='规格',quantity=qty,quoted_price=10,cost_snapshot=5)
                s.add(line); await s.flush(); line_ids.append(line.id)
            await s.commit()
        source=req('GET',f'/order-drafts/source?quote_version_id={vid}')
        assert source['items'][0]['unit_price']=='10.0000'
        body={'quote_version_id':vid,'request_key':str(uuid4()),'items':[{'source_item_id':key,'quantity':qty} for key,qty in zip(line_ids,(10,20))]}
        results=await asyncio.gather(*(asyncio.to_thread(call,'POST','/order-drafts',token=admin,body=body) for _ in range(2)))
        assert all(c==200 for c,_ in results),results
        assert len({r['data']['id'] for _,r in results})==1
        draft=results[0][1]['data']; did=draft['id']; assert draft['status']=='draft' and draft['items'][0]['unit_price']==10
        req('POST','/order-drafts',{**body,'remark':'changed'},expected=409)
        req('GET',f'/order-drafts/{did}',expected=403,token=salesman)
        req('GET',f'/order-drafts/source?quote_version_id={vid}',expected=403,token=salesman)
        async with SessionLocal() as s:
            assert not (await s.execute(select(SalesOrder.id).where(SalesOrder.customer_id.in_(cids)))).scalars().all()
            assert not (await s.execute(select(ReceivablePlan.id).join(SalesOrder,SalesOrder.id==ReceivablePlan.order_id).where(SalesOrder.customer_id.in_(cids)))).scalars().all()
            assert (await s.get(Opportunity,oids[0])).status=='open'
        doc1=req('POST',f'/order-drafts/{did}/documents')
        doc2=req('POST',f'/order-drafts/{did}/documents')
        assert doc1['version']==1 and doc2['version']==2 and doc2['parent_id']==doc1['id']
        assert '草稿' in doc1['title']
        async with SessionLocal() as s:
            doc=await s.get(BizDoc,doc1['id']); snapshot=dict(doc.input_snapshot)
            assert doc.order_id is None and doc.order_draft_id==did and doc.input_snapshot['diffs']==[]
            from app.modules.bizdoc.pdf import render_biz_doc_pdf
            from app.modules.bizdoc.service import doc_pdf_data
            pdf_data=await doc_pdf_data(s,doc)
            assert render_biz_doc_pdf(pdf_data).startswith(b'%PDF')
            # 对外文件的正文里不能残留模板占位符：默认下单模板曾引用
            # {{order.payment_terms}}，而订单草稿根本没有 order 来源，那串语法会**原样
            # 印到客户看的 PDF 上**。只断言 %PDF 头是抓不到这个的（原实现就漏了）。
            assert '{{' not in (pdf_data.get('body') or ''), pdf_data.get('body')
        update={'revision':draft['revision'],'items':[{'source_item_id':row['source_item_id'],'quantity':row['quantity'],'unit_price':row['unit_price'],'specification':row['specification'],'remark':row['remark']} for row in draft['items']]}
        update['items'][0]['quantity']=5
        modified=req('PATCH',f'/order-drafts/{did}',update); assert modified['revision']==2
        req('PATCH',f'/order-drafts/{did}',update,expected=409)
        req('POST',f'/order-drafts/{did}/confirm',{'revision':2,'quote_version_id':vid},expected=422)
        async with SessionLocal() as s:
            v=await s.get(QuoteVersion,vid); v.approval_status='approved'
            q=await s.get(Quote,qid); q.status='approved'; await s.commit()
        req('POST',f'/quote-versions/{vid}/mark-sent',{})
        req('POST',f'/quote-versions/{vid}/convert-to-order',{},expected=422)
        req('POST',f'/quote-versions/{vid}/accept',{})
        req('POST',f'/order-drafts/{did}/confirm',{'revision':2,'quote_version_id':vid},expected=422)
        update['revision']=2; update['items'][0]['quantity']=10
        saved=req('PATCH',f'/order-drafts/{did}',update)
        confirmation={'revision':saved['revision'],'quote_version_id':vid}
        results=await asyncio.gather(*(asyncio.to_thread(call,'POST',f'/order-drafts/{did}/confirm',token=admin,body=confirmation) for _ in range(2)))
        assert all(c==200 for c,_ in results),results
        order_ids={r['data']['order_id'] for _,r in results}; assert len(order_ids)==1
        order_id=next(iter(order_ids))
        assert req('GET',f'/order-drafts/{did}')['status']=='converted'
        req('PATCH',f'/order-drafts/{did}',{**update,'revision':4},expected=422)
        another=req('POST','/order-drafts',{**body,'request_key':str(uuid4())})
        req('POST',f"/order-drafts/{another['id']}/confirm",{'revision':1,'quote_version_id':vid},expected=409)
        async with SessionLocal() as s:
            rows=(await s.execute(select(SalesOrderItem).where(SalesOrderItem.order_id==order_id).order_by(SalesOrderItem.id))).scalars().all()
            assert [r.quote_item_id for r in rows]==line_ids and [r.quantity for r in rows]==[10,20]
            assert (await s.execute(select(func.count()).select_from(ReceivablePlan).where(ReceivablePlan.order_id==order_id))).scalar_one()==1
            from app.modules.bizdoc.service import build_order_sheet_doc
            assert (await build_order_sheet_doc(s,order_id))['diffs']==[]
            assert (await s.get(BizDoc,doc1['id'])).input_snapshot==snapshot
        async with SessionLocal() as s:
            customer=await s.get(Customer,cids[0]); customer.deleted_at=datetime.now(UTC); await s.commit()
        req('POST',f"/order-drafts/{another['id']}/confirm",{'revision':1,'quote_version_id':vid},expected=404)
        async with SessionLocal() as s:
            customer=await s.get(Customer,cids[0]); customer.deleted_at=None; await s.commit()
        v2=req('POST',f'/quotes/{qid}/versions')
        historical=req('GET',f'/order-drafts/source?quote_version_id={vid}')
        assert historical['source']['is_historical']
        old=req('POST','/order-drafts',{**body,'request_key':str(uuid4())})
        req('POST',f"/order-drafts/{old['id']}/confirm",{'revision':1,'quote_version_id':v2['id']},expected=422)
        inq=req('POST','/order-drafts',{'inquiry_id':iid,'request_key':str(uuid4()),'items':[{'source_item_id':iid,'quantity':10000}]})
        assert inq['items'][0]['unit_price'] is None and inq['items'][0]['source_snapshot']['original_quantity']=='10000.000'
        print('OK 订单草稿独立隔离、询价/草稿/历史来源、明细与原单不覆盖、PDF版本、并发去重/过期编辑、客户确认与逐项核对闸门、一版一单、应收一次、重复商品原明细ID溯源')
    finally:
        if cids:
            async with SessionLocal() as s:
                ds=select(OrderDraft.id).where(OrderDraft.customer_id.in_(cids))
                orders=select(SalesOrder.id).where(SalesOrder.customer_id.in_(cids))
                vids=select(QuoteVersion.id).where(QuoteVersion.quote_id.in_(qids))
                for model,condition in (
                    (BizDoc,BizDoc.customer_id.in_(cids)),
                    (Notification,Notification.content.contains(marker) | ((Notification.business_type == 'order') & Notification.business_id.in_(orders))),(BusinessEvent,BusinessEvent.customer_id.in_(cids)),(FollowUp,FollowUp.customer_id.in_(cids)),
                    (AuditLog,AuditLog.after_data.cast(String).contains(marker) | ((AuditLog.business_type=='order_draft') & AuditLog.business_id.in_(ds)) | ((AuditLog.business_type=='order') & AuditLog.business_id.in_(orders)) | ((AuditLog.business_type=='quote') & AuditLog.business_id.in_(qids))),
                    (OrderDraftItem,OrderDraftItem.draft_id.in_(ds)),(OrderDraft,OrderDraft.customer_id.in_(cids)),
                    (ReceivablePlan,ReceivablePlan.order_id.in_(orders)),(OrderStatusHistory,OrderStatusHistory.order_id.in_(orders)),(SalesOrderItem,SalesOrderItem.order_id.in_(orders)),(SalesOrder,SalesOrder.customer_id.in_(cids)),
                    (QuoteCharge,QuoteCharge.quote_version_id.in_(vids)),(QuoteSendLog,QuoteSendLog.quote_version_id.in_(vids)),(QuoteItem,QuoteItem.quote_version_id.in_(vids)),(QuoteVersion,QuoteVersion.quote_id.in_(qids)),(Quote,Quote.id.in_(qids)),
                    (CustomInquiry,CustomInquiry.customer_id.in_(cids)),(OpportunityStageHistory,OpportunityStageHistory.opportunity_id.in_(oids)),(Opportunity,Opportunity.id.in_(oids)),(Customer,Customer.id.in_(cids))):
                    await s.execute(delete(model).where(condition))
                await s.commit()


if __name__=='__main__': asyncio.run(main())
