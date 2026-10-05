"""隔离本机验收：草稿/历史来源、数量分离、快照、幂等、新版结束旧审批。"""
import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import String, delete, select

from app.core.audit import AuditLog
from app.core.deps import CurrentUser
from app.modules.bizdoc.model import BizDoc
from app.modules.bizdoc import service as bizdoc_service
from app.core.config import settings
from app.core.database import SessionLocal
from app.modules.approval.model import ApprovalDefinition, ApprovalInstance, ApprovalRecord
from app.modules.bizdoc.service import build_sample_request_doc
from app.modules.customer.model import Customer
from app.modules.followup.model import FollowUp
from app.modules.inquiry.model import CustomInquiry
from app.modules.notification.model import BusinessEvent, Notification
from app.modules.opportunity.model import Opportunity, OpportunityStage
from app.modules.quote.model import Quote, QuoteVersion, QuoteItem, QuoteCharge
from app.modules.sample.model import SampleRequest, SampleItem
from app.modules.user.model import User
from scripts.check_review_regressions import BASE, call, login


async def main():
    import app.main
    _ = app.main
    assert urlparse(BASE).hostname in {'localhost', '127.0.0.1', '::1'}
    db = urlparse(settings.database_url)
    assert db.hostname in {'localhost', '127.0.0.1', '::1'} and ('test' in db.path.lower() or os.getenv('CI') == 'true')
    assert settings.wecom_push_off and settings.dingtalk_push_off
    assert not settings.scheduler_enabled and not settings.wecom_transfer_enabled
    marker = 'CHKSS'+uuid4().hex[:8]
    cids = []; qids = []; oids = []
    admin = login('admin', 'admin123'); salesman = login('zhangsan', '123456')
    def request(method, path, body=None, token=None, expected=200):
        status, result = call(method, path, body=body, token=token or admin)
        assert status == expected, (method, path, status, result)
        return result.get('data')
    try:
        async with SessionLocal() as s:
            uid = (await s.execute(select(User.id).where(User.username == 'admin'))).scalar_one()
            stage = (await s.execute(select(OpportunityStage.id).order_by(OpportunityStage.sequence))).scalars().first()
            flow = ApprovalDefinition(code=marker, name=marker, business_type='quote_version', config_json={})
            s.add(flow); await s.flush(); definition=flow.id
            customer = Customer(name=marker, owner_id=uid); s.add(customer); await s.flush(); cids.append(customer.id)
            opp = Opportunity(customer_id=customer.id, owner_id=uid, title=marker, stage_id=stage, status='open')
            s.add(opp); await s.flush(); oids.append(opp.id)
            inquiry = CustomInquiry(customer_id=customer.id, opportunity_id=opp.id, title=marker,
                                    inquiry_no=marker, description='原规格', quantity=10000, remark='原备注', created_by=uid)
            s.add(inquiry); await s.flush(); iid = inquiry.id
            quote = Quote(quote_no=marker, opportunity_id=opp.id, customer_id=customer.id, owner_id=uid, status='draft', created_by=uid)
            s.add(quote); await s.flush(); qids.append(quote.id); qid=quote.id
            version = QuoteVersion(quote_id=qid, version_no=1, created_by=uid, created_at=datetime.now(UTC), approval_status='not_submitted')
            s.add(version); await s.flush(); vid=version.id; quote.current_version_id=vid
            item = QuoteItem(quote_version_id=vid, inquiry_id=iid, inquiry_no_snapshot=marker, sku_name_snapshot=marker,
                             spec_snapshot='原规格', quantity=10000, quoted_price=20, cost_snapshot=10, remark='原备注')
            s.add(item); await s.flush(); itemid=item.id
            await s.commit()
        source = request('GET', f'/samples/source?quote_version_id={vid}')
        assert source['items'][0]['original_quantity'] == '10000.000' and not source['source']['is_historical']
        assert 'quoted_price' not in source['items'][0] and source['customer_id'] == cids[0]
        request('GET', f'/samples/source?quote_version_id={vid}', token=salesman, expected=403)
        request('GET', '/samples/source', expected=422)
        request('GET', f'/samples/source?quote_version_id={vid}&inquiry_id={iid}', expected=422)
        body={'quote_version_id':vid, 'request_key':str(uuid4()), 'items':[{'source_item_id':itemid}]}
        request('POST', '/samples/from-source', {**body, 'items':[{'source_item_id':9223372036854775000}]}, expected=422)
        request('POST', '/samples/from-source', {**body, 'items':[{'source_item_id':itemid,'quantity':0}]}, expected=400)
        result = await asyncio.gather(*(asyncio.to_thread(call,'POST','/samples/from-source',token=admin,body=body) for _ in range(2)))
        assert all(code==200 for code,_ in result), result
        sampleids={data['data']['id'] for _,data in result}; assert len(sampleids)==1
        sid=next(iter(sampleids)); sample=request('GET',f'/samples/{sid}')
        assert sample['items'][0]['quantity']==1 and sample['items'][0]['original_quantity']==10000
        request('POST','/samples/from-source',{**body,'items':[{'source_item_id':itemid,'quantity':2}]},expected=409)
        # Add a pending approval to V1; V2 must close it in the same transaction.
        async with SessionLocal() as s:
            v=await s.get(QuoteVersion,vid); v.approval_status='pending'; v.submitted_at=datetime.now(UTC)
            q=await s.get(Quote,qid); q.status='pending_approval'
            instance=ApprovalInstance(definition_id=definition,business_type='quote_version',business_id=vid,
                applicant_id=uid,status='pending',current_node='manager',summary={'quote_no':marker,'version_no':1})
            s.add(instance); await s.flush(); aid=instance.id; await s.commit()
        v2=request('POST',f'/quotes/{qid}/versions'); assert v2['version_no']==2
        history=request('GET',f'/quotes/{qid}/approval-history')
        old=next(row for row in history if row['version_id']==vid)['instance']
        assert old['status']=='withdrawn' and old['finished_at'] and old['summary']['closed_reason']=='superseded'
        assert old['records'][-1]['node_code']=='superseded'
        for action in ('approve','reject','withdraw','transfer'):
            status,_=call('POST',f'/approvals/{aid}/{action}',token=admin,body={'comment':'obsolete','target_user_id':uid})
            assert 400 <= status < 500, (action,status)
        historical=request('GET',f'/samples/source?quote_version_id={vid}')
        assert historical['source']['is_historical']
        chosen=request('POST','/samples/from-source',{'quote_version_id':vid,'request_key':str(uuid4()),
            'items':[{'source_item_id':itemid,'quantity':2,'specification':'本次规格','remark':'本次备注'}]})
        assert chosen['items'][0]['original_quantity']==10000 and chosen['items'][0]['quantity']==2
        assert chosen['items'][0]['source_snapshot']['specification']=='原规格'
        assert chosen['items'][0]['specification']=='本次规格'
        inq=request('POST','/samples/from-source',{'inquiry_id':iid,'request_key':str(uuid4()),'items':[{'source_item_id':iid}]})
        assert inq['items'][0]['quantity']==1 and inq['items'][0]['original_quantity']==10000
        async with SessionLocal() as s:
            original=await s.get(QuoteItem,itemid); assert original.quantity==Decimal(10000) and original.spec_snapshot=='原规格'
            origin=await s.get(CustomInquiry,iid); assert origin.quantity==10000
            origin.description='后来修改的规格'; origin.quantity=20000; original.spec_snapshot='后来修改的报价规格'
            await s.commit()
        async with SessionLocal() as s:
            doc=await build_sample_request_doc(s,chosen['id'])
            assert doc['source']['id']==vid and doc['items'][0]['original_quantity']==10000
            assert doc['items'][0]['quantity']==2 and doc['items'][0]['spec']=='本次规格'
            assert any(d['field']=='规格' and d['before']=='原规格' for d in doc['diffs'])
            operator=await s.get(User,uid)
            document=await bizdoc_service.generate_sample_request_doc(s, sample_request_id=chosen['id'],
                user=CurrentUser(operator, {'sample:manage','sample:view'}, ['admin'], 'all'))
            assert document.source_type=='quote_version' and document.source_id==vid and document.source_version==1
            from app.modules.bizdoc.pdf import render_biz_doc_pdf
            pdf=render_biz_doc_pdf(await bizdoc_service.doc_pdf_data(s,document))
            assert pdf.startswith(b'%PDF')
            await s.commit()

            assert (await s.execute(select(SampleRequest.id).where(SampleRequest.request_key==body['request_key']))).scalars().all()==[sid]
            q=await s.get(Quote,qid); assert q.current_version_id==v2['id'] and q.status=='draft'
        assert request('GET',f"/samples/{inq['id']}")['items'][0]['source_snapshot']['specification']=='原规格'
        # 历史询价也可取用；其原版本数量不被修订的新数量覆盖。
        request('POST', f'/custom-inquiries/{iid}/revise', {'quantity': 30000, 'revision_note': '验收历史来源'})
        historical_inquiry=request('GET', f'/samples/source?inquiry_id={iid}')
        assert historical_inquiry['source']['is_historical'] and Decimal(historical_inquiry['items'][0]['original_quantity'])==20000
        # 同时批准 V2 和建立 V3，锁保证顺序一致：先批准则保留结论，先建新版则旧流程结束。
        async with SessionLocal() as s:
            v=await s.get(QuoteVersion,v2['id']); v.approval_status='pending'
            instance=ApprovalInstance(definition_id=definition,business_type='quote_version',business_id=v.id,
                applicant_id=uid,status='pending',current_node='manager',summary={'quote_no':marker,'version_no':2})
            s.add(instance); await s.flush(); race_aid=instance.id; await s.commit()
        race=await asyncio.gather(
            asyncio.to_thread(call,'POST',f'/approvals/{race_aid}/approve',token=admin,body={}),
            asyncio.to_thread(call,'POST',f'/quotes/{qid}/versions',token=admin,body={}))
        assert race[1][0]==200 and race[0][0] in (200,400,422), race
        async with SessionLocal() as s:
            instance=await s.get(ApprovalInstance,race_aid)
            old_v=await s.get(QuoteVersion,v2['id']); newest=await s.get(QuoteVersion,race[1][1]['data']['id'])
            q=await s.get(Quote,qid)
            assert instance.status in ('approved','withdrawn') and newest.approval_status=='not_submitted'
            assert old_v.approval_status==('approved' if instance.status=='approved' else 'not_submitted')
            assert q.current_version_id==newest.id and q.status=='draft'
        pending=request('GET','/approvals?pending_for_me=true&page_size=100')
        assert all(row['id'] not in (aid,race_aid) for row in pending['items'])
        print('OK 草稿/历史版本、勾选明细、默认1件与原10000分开、快照/规格差异/生成文件、越权与错源拒绝、并发重试去重、新版结束旧审批且旧动作无效')
    finally:
        if cids:
            async with SessionLocal() as s:
                vids=select(QuoteVersion.id).where(QuoteVersion.quote_id.in_(qids))
                samples=select(SampleRequest.id).where(SampleRequest.customer_id.in_(cids))
                aids=select(ApprovalInstance.id).where(ApprovalInstance.business_type=='quote_version',ApprovalInstance.business_id.in_(vids))
                for model, condition in (
                    (BizDoc,BizDoc.customer_id.in_(cids)),
                    (Notification,Notification.content.contains(marker)),(BusinessEvent,BusinessEvent.customer_id.in_(cids)),
                    (FollowUp,FollowUp.customer_id.in_(cids)),
                    (AuditLog,AuditLog.after_data.cast(String).contains(marker) | ((AuditLog.business_type=='quote') & AuditLog.business_id.in_(qids)) | ((AuditLog.business_type=='sample') & AuditLog.business_id.in_(samples))),
                    (ApprovalRecord,ApprovalRecord.approval_instance_id.in_(aids)),(ApprovalInstance,ApprovalInstance.id.in_(aids)),
                    (SampleItem,SampleItem.sample_request_id.in_(samples)),(SampleRequest,SampleRequest.customer_id.in_(cids)),
                    (QuoteCharge,QuoteCharge.quote_version_id.in_(vids)),(QuoteItem,QuoteItem.quote_version_id.in_(vids)),
                    (QuoteVersion,QuoteVersion.quote_id.in_(qids)),(Quote,Quote.id.in_(qids)),
                    (CustomInquiry,CustomInquiry.customer_id.in_(cids)),(Opportunity,Opportunity.id.in_(oids)),(Customer,Customer.id.in_(cids)),
                    (ApprovalDefinition,ApprovalDefinition.code==marker)):
                    await s.execute(delete(model).where(condition))
                await s.commit()


if __name__=='__main__':
    asyncio.run(main())
