import json, subprocess, uuid, sys
sys.path.insert(0,'/Users/zhaorongkai/Desktop/CRM-Sales-Agent-V1.1/backend/scripts')
API='http://127.0.0.1:8069/api/v1'
def call(m,p,tok=None,body=None):
    cmd=['curl','-s','-X',m,f'{API}{p}','-H','Content-Type: application/json']
    if tok: cmd+=['-H',f'Authorization: Bearer {tok}']
    if body is not None: cmd+=['-d',json.dumps(body)]
    return json.loads(subprocess.run(cmd,capture_output=True,text=True).stdout or '{}')
tok=call('POST','/auth/login',body={'username':'admin','password':'admin123'})['data']['access_token']
# 造一批**各种对象**的审计：客户/联系人/商机/需求/报价/明细/任务/跟进/价格规则
c=call('POST','/customers',tok,{'name':'审计页核验客户','region':'浙江','source':'展会','level':'B'})['data']
cid=c['id']; print('客户', cid)
call('PATCH',f'/customers/{cid}',tok,{'level':'A','remark':'核验用'})
ct=call('POST',f'/customers/{cid}/contacts',tok,{'name':'王采购','title':'采购经理','mobile':'13812348000'})['data']
call('PATCH',f'/contacts/{ct["id"]}',tok,{'title':'采购总监'})
opp=call('POST','/opportunities',tok,{'title':'审计页核验商机','customer_id':cid,'expected_amount':50000})['data']
call('PATCH',f'/opportunities/{opp["id"]}',tok,{'competitor':'某某厂'})
inq=call('POST','/custom-inquiries',tok,{'title':'审计页核验需求','customer_id':cid,'quantity':100})['data']
q=call('POST','/quotes',tok,{'customer_id':cid,'opportunity_id':opp['id'],'currency':'CNY'})['data']
call('POST',f'/quote-versions/{q["version_id"]}/items/batch',tok,[
  {'sku_id':6,'quantity':3,'quoted_price':120,'unit_cost':70}])
call('POST',f'/quote-versions/{q["version_id"]}/price-refresh',tok,{})
call('POST',f'/quote-versions/{q["version_id"]}/link-master',tok,{})
call('POST','/followups',tok,{'customer_id':cid,'contact_id':ct['id'],'followup_type':'电话',
  'content':'审计页核验跟进','next_action':'下周再联系','create_task':True,'task_due_at':'2026-10-25T10:00:00+08:00'})
rules=call('GET','/price-rules?page=1&page_size=1',tok)['data']
items=rules.get('items') if isinstance(rules,dict) else rules
if items: call('PATCH',f'/price-rules/{items[0]["id"]}',tok,{'status':'disabled'})
print('审计条数：', call('GET','/audit-logs?page=1&page_size=1',tok)['data'].get('total'))
