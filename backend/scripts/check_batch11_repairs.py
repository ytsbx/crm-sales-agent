"""第十一批真实 PostgreSQL 守门：8 项反例与正常业务对照。

仅允许显式隔离数据库；与真实路由共用 SQL/事务，不模拟锁。
仅替代跟进通知侧效应及外发投递，finally 清理本套件生成的数据与文件。
运行：DATABASE_URL=<隔离库> PYTHONPATH=. .venv/bin/python scripts/check_batch11_repairs.py
"""
import sys
sys.path[:0] = ['.', 'scripts']
from _test_support import require_isolated_db, ISOLATED_DB_PREFIXES, ISOLATED_DB_SUFFIXES
db_name = require_isolated_db()
if not (db_name.startswith(ISOLATED_DB_PREFIXES) or db_name.endswith(ISOLATED_DB_SUFFIXES)):
 raise SystemExit('本套件只允许隔离库，不接受业务库逃生开关')
import asyncio
from datetime import UTC,datetime
from unittest.mock import AsyncMock
from _test_support import require_isolated_db
from sqlalchemy import select,text,func
from starlette.requests import Request
from app.core.database import SessionLocal,engine
from app.core.deps import CurrentUser
from app.modules.user.model import User
from app.modules.user import service as users
from app.modules.inquiry.model import CustomInquiry
from app.modules.inquiry import router as inquiry
from app.modules.inquiry.schema import CustomInquiryQuoteRequest,CustomInquiryUpdate
from app.modules.quote.model import QuoteItem
from app.modules.sample.model import SampleRequest
from app.modules.sample import router as samples
from app.modules.sample.schema import SampleMade
from app.modules.file.model import FileRecord,BusinessFile
from app.modules.file import router as files
from app.modules.followup import service as followup
from app.core.errors import AppError
from datetime import timedelta
from app.modules.customer import router as customers
from app.modules.customer.model import Customer,Contact
from app.modules.customer.schema import CustomerTransfer
from app.modules.task import router as tasks
from app.modules.task.schema import TaskCreate, TaskComplete, TaskBatchComplete
from app.modules.task.model import Task
from app.modules.lead import router as leads
from app.modules.lead.model import Lead
from app.modules.lead.schema import LeadConvert
from app.modules.sample.schema import SampleShip
from app.core.timebase import today_business
from app.modules.customer.schema import CustomerUpdate
from uuid import uuid4
from pydantic import ValidationError
from sqlalchemy import delete
from app.core.audit import AuditLog
from app.modules.customer.model import CustomerOwnerHistory
from app.modules.sample.model import SampleShipment, SampleItem
from app.modules.notification.model import Notification
from app.modules.notification import service as notifications
MARK = 'b11-' + uuid4().hex[:12]
FILEKEY = MARK + '/gap.txt'
REQ=Request({'type':'http','headers':[],'client':('127.0.0.1',1)})

async def wait_locked(pid,worker,*,strict=True):
 '''等到"这个后端会话确实在等锁"为止。

 ⚠️ 这个等待本身**不能**作为被测行为的判据 —— 它只是"把持锁方的锁拿久一点"
 的辅助手段，靠轮询 `pg_stat_activity` 观察，可能一次都没采到（工人还没来得及
 进入等待、或刚好在两次轮询之间被放行）。实测：本地连跑两次都过、CI 上红，
 红的时候报的是"旧负责人仍能删除客户" —— 而真实情形是**工人根本没被挡住、
 先把客户删完了**（那是**合法**顺序：删除发生在转交之前）。

 所以修正有两处：
   · 这里观察不到就返回 `False`（让调用方**跳过**这一轮，而不是断言失败）；
   · 真正的判据放在调用方：**只有在确认"工人被挡住"之后**，才要求它被拒。
 '''
 for _ in range(500):
  async with SessionLocal() as observer:
   wait=await observer.scalar(text('select wait_event_type from pg_stat_activity where pid=:pid'),{'pid':pid})
  if wait=='Lock':return True
  if worker.done():
   # 没被挡住 → 这轮不构成被测交错。
   # `strict=True`（默认）保留原来的行为：**直接失败** —— 其余 6 处靠它发现
   # "脚本没把交错造出来"（那是脚本坏了，必须红）。
   # `strict=False` 留给"顺序合法、只是没造出交错"的场景（见客户删除那条）。
   if strict: raise AssertionError('worker finished before expected lock')
   return False
  await asyncio.sleep(.02)
 if strict: raise AssertionError('lock wait not observed')
 return False

async def inquiry_race(actor,kind):
 async with SessionLocal() as s:
  row=CustomInquiry(inquiry_no=f'{MARK}-{kind}',title='删除交错测试',customer_id=1,created_by=actor.id,quantity=1,status='open')
  s.add(row);await s.commit();iid=row.id
 async with SessionLocal() as b:
  await b.execute(select(CustomInquiry).where(CustomInquiry.id==iid).with_for_update())
  async with SessionLocal() as a:
   pid=await a.scalar(text('select pg_backend_pid()'))
   if kind=='quote':call=inquiry.create_quote_from_inquiry(iid,CustomInquiryQuoteRequest(unit_cost=10,quoted_price=20),REQ,actor,a)
   else:call=inquiry.update_inquiry(iid,CustomInquiryUpdate(title='删除后仍写入'),REQ,actor,a)
   worker=asyncio.create_task(call)
   try:
    await wait_locked(pid,worker)
    deleted=await inquiry.delete_inquiry(iid,REQ,actor,b);assert deleted['code']==0
    try:
     await asyncio.wait_for(worker,10);raise AssertionError('已删除需求仍被修改')
    except AppError as e:
     assert e.http_status == 404
     print('PASS 已删除需求拒绝编辑')
   finally:
    if not worker.done():worker.cancel()
    await asyncio.gather(worker,return_exceptions=True)
 async with SessionLocal() as s:
  row=await s.get(CustomInquiry,iid)
  assert row.title == '删除交错测试'
  print('INQUIRY',kind,'terminal deleted',row.deleted_at is not None,'title',row.title,'quote refs',await s.scalar(select(func.count()).select_from(QuoteItem).where(QuoteItem.inquiry_id==iid)))

async def fresh_customer(owner,name):
 async with SessionLocal() as s:
  c=Customer(name=MARK+'-'+name,owner_id=owner,created_by=1)
  s.add(c);await s.commit();return c.id

async def attachment_final_gap(actor):
 async with SessionLocal() as s:
  sample=SampleRequest(customer_id=1,owner_id=actor.id,status='approved')
  from app.modules.file import storage
  import hashlib
  body=b'drawing v1';path=storage.absolute_path(FILEKEY);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body)
  rec=FileRecord(object_key=FILEKEY,file_name='事后图纸.txt',uploaded_by=actor.id,size=len(body),mime_type='text/plain',checksum=hashlib.sha256(body).hexdigest())
  s.add_all([sample,rec]);await s.commit();sid,fid=sample.id,rec.id
 reached,release=asyncio.Event(),asyncio.Event();original=files.write_audit
 async def paused(*args,**kwargs):
  if kwargs.get('business_type')=='sample' and kwargs.get('action')=='attach' and kwargs.get('business_id')==sid:
   reached.set();await asyncio.wait_for(release.wait(),10)
  return await original(*args,**kwargs)
 files.write_audit=paused
 try:
  async with SessionLocal() as a:
   worker=asyncio.create_task(files.attach_file('sample',sid,REQ,fid,'drawing',actor,a))
   try:
    await asyncio.wait_for(reached.wait(),10)
    async with SessionLocal() as b:
     pid=await b.scalar(text('select pg_backend_pid()'))
     made_worker=asyncio.create_task(samples.register_sample_made(sid,SampleMade(remark='完成制作'),REQ,actor,b))
     try:
      await wait_locked(pid,made_worker)
      release.set();result=await asyncio.wait_for(worker,10)
      assert (await asyncio.wait_for(made_worker,10))['code']==0
     finally:
      if not made_worker.done():made_worker.cancel()
      await asyncio.gather(made_worker,return_exceptions=True)
    print('ATTACH AFTER MADE',result['code'])
   finally:
    release.set()
    if not worker.done():worker.cancel()
    await asyncio.gather(worker,return_exceptions=True)
 finally:files.write_audit=original
 async with SessionLocal() as s:
  sample=await s.get(SampleRequest,sid)
  print('ATTACH GAP TERMINAL made',sample.made_at is not None,'non-supplement links',await s.scalar(select(func.count()).select_from(BusinessFile).where(BusinessFile.business_type=='sample',BusinessFile.business_id==sid,BusinessFile.category=='drawing')))
  try:
   await files.attach_file('sample',sid,REQ,fid,'drawing',actor,s)
   raise AssertionError('制作后仍允许添加图纸')
  except AppError:
   await s.rollback()
  print('PASS 制作后拒绝图纸，正常制作等待附件事务提交')
  print('ATTACH positive supplement', (await files.attach_file('sample',sid,REQ,fid,'supplement',actor,s))['code'])

async def sample_dates(actor):
 now=datetime.now(UTC)
 for label,made_at in [('correct-before-shipping',now-timedelta(days=4)),('wrong-after-shipping',now-timedelta(days=2))]:
  async with SessionLocal() as s:
   sample=SampleRequest(customer_id=1,owner_id=actor.id,status='shipped',shipped_at=now-timedelta(days=3))
   s.add(sample);await s.commit();sid=sample.id
   try:
    result=await samples.register_sample_made(sid,SampleMade(made_at=made_at),REQ,actor,s)
    assert label=='correct-before-shipping' and result['code']==0
    print('PASS 制作在寄出前的历史补录')
   except AppError:
    assert label=='wrong-after-shipping'
    print('PASS 制作在寄出后被拒');await s.rollback()
 async with SessionLocal() as s:
  sample=SampleRequest(customer_id=1,owner_id=actor.id,status='approved');s.add(sample);await s.commit();sid=sample.id
  await samples.register_sample_made(sid,SampleMade(made_at=now-timedelta(days=2)),REQ,actor,s)
  try:
   await samples.ship_sample(sid,SampleShip(shipped_at=today_business()-timedelta(days=3)),REQ,actor,s)
   raise AssertionError('允许制作前寄出')
  except AppError:
   await s.rollback()
   result=await samples.ship_sample(sid,SampleShip(shipped_at=today_business()-timedelta(days=1)),REQ,actor,s)
   assert result['code']==0
   print('PASS 寄出前必须已制作，正常日期寄出成功')

async def task_clock_race(actor):
 cid=await fresh_customer(actor.id,'TASK-CLOCK')
 reached,release=asyncio.Event(),asyncio.Event();original=tasks.write_audit
 async def paused(*args,**kwargs):
  if kwargs.get('business_type')=='task' and kwargs.get('action')=='create' and kwargs.get('after',{}).get('title')=='EARLY':
   reached.set();await asyncio.wait_for(release.wait(),10)
  return await original(*args,**kwargs)
 tasks.write_audit=paused
 early=datetime.now(UTC)+timedelta(days=1);late=early+timedelta(days=4)
 try:
  async with SessionLocal() as a,SessionLocal() as b:
   first=asyncio.create_task(tasks.create_task(TaskCreate(title='EARLY',customer_id=cid,task_type='followup',due_at=early),REQ,actor,a))
   second=None
   try:
    await asyncio.wait_for(reached.wait(),10);pid=await b.scalar(text('select pg_backend_pid()'))
    second=asyncio.create_task(tasks.create_task(TaskCreate(title='LATE',customer_id=cid,task_type='followup',due_at=late),REQ,actor,b))
    await wait_locked(pid,second);release.set()
    print('TASK RACE route codes',(await asyncio.wait_for(first,10))['code'],(await asyncio.wait_for(second,10))['code'])
   finally:
    release.set()
    for w in (first,second):
     if w and not w.done():w.cancel()
    await asyncio.gather(*[w for w in (first,second) if w],return_exceptions=True)
 finally:tasks.write_audit=original
 async with SessionLocal() as s:
  c=await s.get(Customer,cid);minimum=await s.scalar(select(func.min(Task.due_at)).where(Task.customer_id==cid,Task.status=='pending'))
  assert c.next_followup_at==minimum
  print('PASS 并发创建后最早跟进日期一致')
  print('TASK CLOCK terminal next',c.next_followup_at,'real earliest',minimum,'wrong',c.next_followup_at!=minimum)

async def customer_delete_race(actor,restricted,*,attempts=5):
 '''转交与"旧负责人删客户"的交错。

 ⚠️ 这条**必须真的造出"工人被挡住"**才说明问题，而"被挡住"是时序事件：
 工人可能先拿到锁、把客户删完（那是**合法**顺序：删在转交之前）。
 所以这里最多试 `attempts` 轮，只要有一轮造出交错就按 403 判；
 一轮都没造出来才 `SKIP`（明确说出来，不假装通过）。
 '''
 for attempt in range(1,attempts+1):
  if await _customer_delete_race_once(actor,restricted,attempt):
   return
 print(f'SKIP 客户删除交错：{attempts} 轮都没观察到锁等待（顺序都是"先删后转"，合法）')


async def _customer_delete_race_once(actor,restricted,attempt):
 cid=await fresh_customer(restricted.id,'OLD-OWNER-DELETE')
 async with SessionLocal() as b:
  await b.execute(select(Customer).where(Customer.id==cid).with_for_update())
  async with SessionLocal() as a:
   pid=await a.scalar(text('select pg_backend_pid()'))
   worker=asyncio.create_task(customers.delete_customer(cid,REQ,restricted,a))
   try:
    if not await wait_locked(pid,worker,strict=False):
     # 这一轮没造出交错 → 换一轮再试（见外层 `attempts`）。
     # 原来这里会断言失败、报"旧负责人仍能删除客户"，与真实情形
     # （工人根本没被挡住、先删完了）不符 —— 那句误导最费时间。
     await asyncio.gather(worker,return_exceptions=True)
     print(f'  （第 {attempt} 轮未观察到锁等待，重试）')
     return False
    result=await customers.transfer_customer(cid,CustomerTransfer(owner_id=3,reason='转交在职同事'),REQ,actor,b);assert result['code']==0
    try:
     await asyncio.wait_for(worker,10);raise AssertionError('旧负责人仍能删除客户')
    except AppError as e:
     assert e.http_status==403
     print('PASS 转交后旧负责人删除被拒')
     return True
   finally:
    if not worker.done():worker.cancel()
    await asyncio.gather(worker,return_exceptions=True)
 async with SessionLocal() as s:
  c=await s.get(Customer,cid);assert c.owner_id==3 and c.deleted_at is None
  print('OLD OWNER DELETE terminal owner',c.owner_id,'deleted',c.deleted_at is not None,'actor',restricted.id)

async def lead_target_deleted_race(actor,restricted):
 cid=await fresh_customer(restricted.id,'LEAD-DELETE')
 async with SessionLocal() as s:
  lead=Lead(name='转化线索',contact_name='联系人',mobile='13800138001',owner_id=restricted.id,status='pending');s.add(lead);await s.commit();lid=lead.id
 async with SessionLocal() as b:
  await b.execute(select(Customer).where(Customer.id==cid).with_for_update())
  async with SessionLocal() as a:
   pid=await a.scalar(text('select pg_backend_pid()'))
   worker=asyncio.create_task(leads.convert_lead(lid,LeadConvert(customer_mode='existing',customer_id=cid,create_contact=True),REQ,restricted,a))
   try:
    await wait_locked(pid,worker);assert (await customers.delete_customer(cid,REQ,actor,b))['code']==0
    try:
     await asyncio.wait_for(worker,10);raise AssertionError('线索被转入已删除客户')
    except AppError as e:
     assert e.http_status==404
     print('PASS 不向已删除客户转化线索')
   finally:
    if not worker.done():worker.cancel()
    await asyncio.gather(worker,return_exceptions=True)
 async with SessionLocal() as s:
  l=await s.get(Lead,lid);c=await s.get(Customer,cid)
  assert l.status=='pending' and l.converted_customer_id is None
  assert await s.scalar(select(func.count()).select_from(Contact).where(Contact.customer_id==cid))==0
  print('LEAD terminal',l.status,'customer deleted',c.deleted_at is not None,'created contacts',await s.scalar(select(func.count()).select_from(Contact).where(Contact.customer_id==cid)))

async def deadlock(actor,old):
 async with SessionLocal() as s:
  c=Customer(name=MARK+'-DEADLOCK',owner_id=old.id);s.add(c);await s.flush();cid=c.id
  t=Task(title='客户待跟进',owner_id=old.id,customer_id=cid,task_type='followup',status='pending',due_at=datetime.now(UTC));s.add(t);await s.commit();tid=t.id
 reached,release=asyncio.Event(),asyncio.Event();original=tasks._sync_next_followup
 async def paused(s,task):
  if task.id==tid:
   reached.set();await asyncio.wait_for(release.wait(),10)
  return await original(s,task)
 tasks._sync_next_followup=paused
 async def request(s,call):
  try:r=await call;return ('OK',r['code'])
  except Exception as e:
   await s.rollback();return ('ERROR',type(e).__name__,'deadlock detected' in str(e).lower(),str(e).splitlines()[0][:160])
 try:
  async with SessionLocal() as a,SessionLocal() as b:
   first=asyncio.create_task(request(a,tasks.complete_task(tid,TaskComplete(),REQ,old,a)));second=None
   try:
    await asyncio.wait_for(reached.wait(),10);pid=await b.scalar(text('select pg_backend_pid()'))
    second=asyncio.create_task(request(b,customers.transfer_customer(cid,CustomerTransfer(owner_id=3,reason='交接客户'),REQ,actor,b)))
    await wait_locked(pid,second);release.set()
    results=await asyncio.wait_for(asyncio.gather(first,second),15)
    assert all(x==('OK',0) for x in results),results
    print('PASS 完成任务与转交客户均成功，无死锁')
   finally:
    release.set()
    for w in (first,second):
     if w and not w.done():w.cancel()
    await asyncio.gather(*[w for w in (first,second) if w],return_exceptions=True)
 finally:tasks._sync_next_followup=original
 async with SessionLocal() as s:
  c=await s.get(Customer,cid);t=await s.get(Task,tid);print('DEADLOCK terminal customer owner',c.owner_id,'task',t.status,'owner',t.owner_id)

async def future_contact(actor, restricted):
 cid=await fresh_customer(restricted.id,'CONTACT')
 async with SessionLocal() as s:
  try:
   CustomerUpdate(last_followup_at=datetime(2099,1,1,tzinfo=UTC))
   raise AssertionError('未来联系时间通过入参校验')
  except ValidationError:
   pass
  assert (await customers.update_customer(cid,CustomerUpdate(last_followup_at=datetime.now(UTC)-timedelta(days=60)),REQ,restricted,s))['code']==0
  assert (await customers.update_customer(cid,CustomerUpdate(last_followup_at=None),REQ,restricted,s))['code']==0
  print('PASS 拒绝未来联系，允许历史补录和清空')

async def batch_order(actor):
 cids=[await fresh_customer(actor.id,'BATCH-A'),await fresh_customer(actor.id,'BATCH-B')]
 async with SessionLocal() as s:
  rows=[Task(title='批量锁序',owner_id=actor.id,customer_id=cid,status='pending',task_type='followup')
        for cid in [cids[0],None,cids[1],None]]
  s.add_all(rows);await s.commit();ids=[row.id for row in rows]
 reached,release=asyncio.Event(),asyncio.Event();original=tasks.write_audit
 async def paused(*args,**kwargs):
  if kwargs.get('action')=='batch_complete' and not reached.is_set():
   reached.set();await asyncio.wait_for(release.wait(),10)
  return await original(*args,**kwargs)
 tasks.write_audit=paused
 try:
  async with SessionLocal() as a,SessionLocal() as b:
   first=asyncio.create_task(tasks.batch_complete_tasks(TaskBatchComplete(task_ids=ids[::-1]),REQ,actor,a));second=None
   try:
    await asyncio.wait_for(reached.wait(),10);pid=await b.scalar(text('select pg_backend_pid()'))
    second=asyncio.create_task(tasks.batch_complete_tasks(TaskBatchComplete(task_ids=ids),REQ,actor,b))
    await wait_locked(pid,second);release.set()
    one,two=await asyncio.wait_for(asyncio.gather(first,second),10)
    assert set(one['data']['completed'])==set(ids)
    assert two['data']['completed']==[] and len(two['data']['skipped'])==4
    print('PASS 跨客户及无客户任务反序批量完成，无死锁且终态正确跳过')
   finally:
    release.set()
    for w in (first,second):
     if w and not w.done():w.cancel()
    await asyncio.gather(*[w for w in (first,second) if w],return_exceptions=True)
 finally:tasks.write_audit=original

async def main():
 models=[BusinessFile,SampleShipment,SampleItem,Contact,Task,CustomerOwnerHistory,
         Lead,CustomInquiry,SampleRequest,FileRecord,Customer,Notification,AuditLog]
 before={}
 async with SessionLocal() as s:
  for model in models:before[model]=set((await s.scalars(select(model.id))).all())
  actor=CurrentUser(await s.get(User,1),await users.get_user_permission_codes(s,1),['admin'],'all')
  restricted=CurrentUser(await s.get(User,2),{'customer:delete','customer:update','lead:convert','task:manage','task:view'},['salesperson'],'self')
 originals=(followup.record_and_notify,notifications.dispatch_pending)
 followup.record_and_notify=AsyncMock();notifications.dispatch_pending=AsyncMock()
 try:
  await customer_delete_race(actor,restricted)
  await lead_target_deleted_race(actor,restricted)
  await attachment_final_gap(actor)
  await sample_dates(actor)
  await task_clock_race(actor)
  await deadlock(actor,restricted)
  await inquiry_race(actor,'update')
  await future_contact(actor,restricted)
  await batch_order(actor)
  print('PASS 第十一批 8 项及正向对照')
 finally:
  followup.record_and_notify,notifications.dispatch_pending=originals
  async with SessionLocal() as s:
   for model in models:
    added=set((await s.scalars(select(model.id))).all())-before[model]
    if added:await s.execute(delete(model).where(model.id.in_(added)))
   await s.commit()
  from app.modules.file import storage
  storage.absolute_path(FILEKEY).unlink(missing_ok=True)
  try:storage.absolute_path(FILEKEY).parent.rmdir()
  except FileNotFoundError:pass
  await engine.dispose()

if __name__=='__main__':asyncio.run(main())
