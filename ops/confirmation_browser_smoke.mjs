import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { spawn } from 'node:child_process'
import { readFileSync, existsSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, extname, resolve } from 'node:path'

// Run after frontend build: node ops/confirmation_browser_smoke.mjs frontend/dist
// Uses an isolated browser profile and in-memory mock APIs; no business database is used.
const executable=process.env.CRM_SMOKE_BROWSER ?? [
  '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
].find(existsSync)
assert.ok(executable, 'set CRM_SMOKE_BROWSER to an installed Chrome/Edge binary')
assert.ok(process.argv[2], 'provide the built frontend dist directory')
const dist=resolve(process.argv[2])
const profile=mkdtempSync(join(tmpdir(),'crm-confirm-browser-'))
const user={id:1,name:'隔离测试用户',username:'isolated',roles:['admin'],permissions:[],data_scope:'all'}
const task={id:7,title:'确认保护测试任务',status:'pending',status_label:'待处理',owner_id:1,priority:'normal',due_at:new Date().toISOString(),updated_at:new Date().toISOString()}
let completed=0, redispatched=0, markedRead=0, restored=0, browser, socket
const completedBodies=[], batchBodies=[]
const claimedLeads=[], claimedCustomers=[]
const approvalsApproved=[], approvalsRejected=[]
const sampleRequests=[]
const wecomRequests=[]
const wecomBindings=[]
const recycleRequests=[]
const quoteWithdrawals=[]
const settingsRequests=[]
const customerOwnerWrites=[]
const ownerRequests=[]
const ownerUsers=Array.from({length:201},(_,i)=>({id:111+i,name:i===0?'在职员工甲':i===1?'在职员工乙':i===200?'末页在职员工':`分页员工${i}`,username:i===200?'last.owner':`owner${i}`,status:'active'}))
const quoteVersion=id=>({id,quote_id:121,version_no:id===131?2:1,subtotal_amount:100,charge_amount:0,discount_amount:0,total_amount:100,logistics_amount:0,other_charge_amount:0,pricing_basis:'actual_pass_through',approval_status:'pending',approval_required:true,created_at:new Date().toISOString()})
const recycleRecord=(id,name,extra={})=>({id,name,status_label:'已删除',deleted_by_pending:true,deleted_at:new Date().toISOString(),...extra})
const wecomContact={id:92,external_userid:'isolated-contact',name:'隔离企微联系人',corp_name:'隔离公司',normalize_status:'pending',normalize_status_label:'待确认',followers:[]}
const sampleStatuses={61:'pending',62:'pending',63:'rejected',64:'approved',65:'shipped',66:'shipped',67:'rejected'}
const sampleFixture=id=>({id,status:sampleStatuses[id]??'pending',status_label:'隔离样品状态',customer_name:'隔离样品客户',items:[],shipments:[],confirm_status:'pending'})
const publicCustomer={id:9,name:'领取确认测试客户',pool_status:'public',status:'active',tags:[],owner_id:null}
const server=createServer(async (req,res)=>{
  const url=new URL(req.url,'http://localhost')
  const path=url.pathname
  if(path.startsWith('/api/')) {
    let body={}
    if(req.method==='POST') {
      let raw='';for await (const chunk of req) raw+=chunk
      body=raw ? JSON.parse(raw) : {}
    }
    let data=[]
    if(path.endsWith('/public-pool/recycle-candidates/batch-decide') && req.method==='POST') {settingsRequests.push({path,body});data={done:body.candidate_ids.map(id=>({candidate_id:id,status:'approved'})),failed:[]}}
    else if(path.endsWith('/public-pool/recycle-candidates')) data={items:[141,142,143].map(id=>({id,customer_id:id,customer_name:`隔离回收候选${id}`,owner_name:'隔离负责人',status:'pending',protection:[],status_label:'待复核'})),total:3}
    else if(path.endsWith('/public-pool/run-recycle') && req.method==='POST') {settingsRequests.push({path,body});data={nominated_count:0,protected_count:0}}
    else if(path.endsWith('/tasks/run-auto-rules') && req.method==='POST') {settingsRequests.push({path,body});data={created_count:0,failed_rule_count:0}}
    else if(path.endsWith('/customers/batch-transfer') && req.method==='POST') {customerOwnerWrites.push({path,body});data={affected:body.customer_ids.length}}
    else if(path==='/api/v1/customers' && req.method==='POST') {customerOwnerWrites.push({path,body});data={id:151,...body}}
    else if(path==='/api/v1/customers') data={items:[publicCustomer],total:1}
    else if(path.endsWith('/auth/me')) data=user
    else if(path.endsWith('/notifications/unread-count')) data={count:0}
    else if(path.endsWith('/notifications')) data={items:[{id:41,title:'补投确认测试通知',read:false,wecom_status:'failed',can_redispatch:true,business_type:'customer',business_id:9,created_at:new Date().toISOString()}],total:1}
    else if(path.endsWith('/notifications/41/redispatch') && req.method==='POST') {redispatched++;data={sent:true}}
    else if(path.endsWith('/notifications/41/read') && req.method==='POST') {markedRead++;data={}}
    else if(path.endsWith('/dashboard/summary')) data={todo_count:1,stale_customer_days:30,stale_customer_count:0}
    else if(path.endsWith('/dashboard/team')) data={is_team_view:false}
    else if(path.endsWith('/dashboard/tasks')) data=[task]
    else if(path.endsWith('/tasks/7/complete') && req.method==='POST') { completed++; completedBodies.push(body); data={...task,status:'done'} }
    else if(path.endsWith('/tasks')) data={items:[task],total:1,page:1,page_size:20}
    else if(path.endsWith('/tasks/batch-complete') && req.method==='POST') { batchBodies.push(body); data={completed:[7],skipped:[]} }
    else if(path.endsWith('/opportunities')) data={items:[],total:0}
    else if(path.endsWith('/quotes/121/versions')) data=[quoteVersion(131),quoteVersion(130)]
    else if(/\/quote-versions\/(130|131)$/.test(path)) data={version:quoteVersion(Number(path.split('/').at(-1))),quote:{id:121,quote_no:'ISO-QUOTE-121',customer_id:9,customer_name:'隔离报价客户',status:'pending',status_label:'审批中',current_version_id:131},items:[],charges:[],approval:null}
    else if(/\/quote-versions\/(130|131)\/withdraw-approval$/.test(path) && req.method==='POST') {quoteWithdrawals.push(Number(path.split('/').at(-2)));data=null}
    else if(path.endsWith('/recycle-bin/leads')) data={items:[{id:21,name:'恢复确认测试线索',status_label:'待处理',created_at:new Date().toISOString(),deleted_at:new Date().toISOString(),deleted_by_pending:true}],total:1,page:1,page_size:10}
    else if(path.endsWith('/leads/21/restore') && req.method==='POST') {restored++;data=null}
    else if(path.endsWith('/recycle-bin/products')) data={items:[recycleRecord(101,'隔离恢复产品',{deleted_sku_count:1})],total:1}
    else if(path.endsWith('/recycle-bin/skus')) data={items:[recycleRecord(102,'隔离恢复SKU',{sku_code:'ISO-SKU-102',product_deleted:false,removed_via:'direct'}),recycleRecord(107,'随产品删除SKU',{sku_code:'ISO-SKU-107',product_deleted:true,removed_via:'with_product'})],total:2}
    else if(path.endsWith('/recycle-bin/customers')) data={items:[recycleRecord(103,'隔离恢复客户',{removed_via:'direct',original_owner_id:1,original_owner_name:'隔离负责人',original_owner_active:true,merged_into:null,final_target:null}),recycleRecord(106,'待重新分配客户',{removed_via:'direct',original_owner_id:2,original_owner_name:'已停用员工',original_owner_active:false,merged_into:null,final_target:null}),recycleRecord(108,'被合并客户',{removed_via:'merged',original_owner_active:true,merged_into:{id:9,name:'合并目标',visible:true,state:'ok'},final_target:null})],total:3}
    else if(path.endsWith('/recycle-bin/contracts')) data={items:[recycleRecord(105,'隔离恢复合同',{doc_no:'ISO-CONTRACT-105',title:'隔离恢复合同'})],total:1}
    else if(/\/(products|skus|customers|samples|contract-documents)\/(101|102|103|104|105|106)\/restore$/.test(path) && req.method==='POST') {recycleRequests.push({path,body});data=path.includes('/products/')?{restored_skus:[],skipped_skus:[]}:null}
    else if(path.endsWith('/users')) {
      ownerRequests.push(Object.fromEntries(url.searchParams))
      const keyword=(url.searchParams.get('keyword')??'').toLowerCase()
      const matches=ownerUsers.filter(u=>u.name.toLowerCase().includes(keyword)||u.username.includes(keyword))
      const page=Number(url.searchParams.get('page')??1),size=Number(url.searchParams.get('page_size')??20)
      data={items:matches.slice((page-1)*size,page*size),total:matches.length}
    }
    else if(path.endsWith('/leads')) data={items:[{id:31,name:'线索甲',status:'pending',status_label:'待处理',owner_id:null},{id:32,name:'线索乙',status:'pending',status_label:'待处理',owner_id:null}],total:2,page:1,page_size:10}
    else if(/\/leads\/\d+\/claim$/.test(path) && req.method==='POST') {const id=Number(path.split('/').at(-2));claimedLeads.push(id);data={id,owner_id:1}}
    else if(path.endsWith('/customers/9')) data=publicCustomer
    else if(path.endsWith('/customers/9/claim') && req.method==='POST') {claimedCustomers.push(9);data={...publicCustomer,owner_id:1}}
    else if(path.endsWith('/approvals')) data={items:[51,52].map(id=>({id,quote_no:`隔离审批-${id}`,version_id:id,quote_id:id,version_no:1,status:'pending',status_label:'待审批',can_approve:true,summary:{}})),total:2,page:1,page_size:50}
    else if(/\/approvals\/\d+\/approve$/.test(path) && req.method==='POST') {approvalsApproved.push(Number(path.split('/').at(-2)));data=null}
    else if(/\/approvals\/\d+\/reject$/.test(path) && req.method==='POST') {approvalsRejected.push({id:Number(path.split('/').at(-2)),comment:url.searchParams.get('comment')});data=null}
    else if(path.endsWith('/samples')) data=url.searchParams.get('include_deleted')==='true'?{items:[recycleRecord(104,'隔离恢复样品',{customer_name:'隔离恢复样品客户'})],total:1}:{items:[],total:0,page:1,page_size:20}
    else if(/\/samples\/\d+$/.test(path)) data=sampleFixture(Number(path.split('/').at(-1)))
    else if(/\/samples\/\d+\/(approve|resubmit|ship|sign|revise)$/.test(path) && req.method==='POST') {
      const id=Number(path.split('/').at(-2)),action=path.split('/').at(-1)
      sampleRequests.push({id,action,body});data=sampleFixture(action==='revise'?id+100:id)
    }
    else if(path.endsWith('/integrations/wecom/readiness')) data={configured:{corp_id:true,agent_id:true,contact_secret:true,external_contact_secret:true,callback:true},counts:{wecom_users:0,external_contacts:0,follow_relations:0,unbound_contacts:0,unmatched_users:0},can_sync_contact:true,can_sync_external:true}
    else if(path.endsWith('/integrations/wecom/unbound-contacts')) data={items:[wecomContact],total:1,page:1,page_size:20}
    else if(path.endsWith('/integrations/wecom/sync-jobs')) data={items:[],total:0,page:1,page_size:20}
    else if(path.endsWith('/integrations/wecom/unbound-contacts/92/candidates')) data={contact:wecomContact,candidates:[{id:93,name:'隔离匹配客户甲',score:80,reasons:[]},{id:94,name:'隔离匹配客户乙',score:85,reasons:[]}]}
    else if(path.endsWith('/integrations/wecom/unbound-contacts/92/bind-customer') && req.method==='POST') {wecomBindings.push({contact_id:92,...body});data={contact_id:92,customer_id:body.customer_id,customer_name:'隔离匹配客户乙'}}
    else if(/\/integrations\/wecom\/sync-(departments|users|external-contacts|follow-relations)$/.test(path) && req.method==='POST') {
      wecomRequests.push(path.split('/').at(-1));data={id:91,status:'success',success_count:0,fail_count:0}
    }
    if(req.method==='POST' && (path.endsWith('/notifications/41/redispatch') || /\/leads\/\d+\/claim$/.test(path))) await new Promise(resolve=>setTimeout(resolve,700))
    res.writeHead(200,{'Content-Type':'application/json'}); res.end(JSON.stringify({code:0,message:'ok',data})); return
  }
  const file=path.startsWith('/assets/') ? join(dist,path) : extname(path) ? join(dist,path) : join(dist,'index.html')
  if(!file.startsWith(dist) || !existsSync(file)) {res.writeHead(404);res.end();return}
  res.writeHead(200,{'Content-Type':extname(file)==='.js'?'application/javascript':extname(file)==='.css'?'text/css':extname(file)==='.svg'?'image/svg+xml':'text/html'});res.end(readFileSync(file))
})
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve))
const port=server.address().port
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms))
try {
  browser=spawn(executable,['--headless=new','--no-first-run','--no-default-browser-check','--disable-background-networking','--disable-extensions','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'],{stdio:'ignore'})
  for(let i=0;!existsSync(join(profile,'DevToolsActivePort'));i++){if(i>100)throw Error('browser timeout');await sleep(100)}
  const debug=readFileSync(join(profile,'DevToolsActivePort'),'utf8').split('\n')[0]
  const target=await (await fetch(`http://127.0.0.1:${debug}/json/new?about:blank`,{method:'PUT'})).json()
  socket=new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject})
  let next=1;const pending=new Map()
  socket.onmessage=event=>{const data=JSON.parse(event.data);if(data.id){const p=pending.get(data.id);if(p){clearTimeout(p.timer);pending.delete(data.id);data.error?p.reject(Error(JSON.stringify(data.error))):p.resolve(data.result)}}}
  const send=(method,params={})=>new Promise((resolve,reject)=>{
    const id=next++
    const timer=setTimeout(()=>{pending.delete(id);reject(Error(`browser command timed out: ${method}`))},15000)
    pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({id,method,params}))
  })
  const evaluate=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error((r.exceptionDetails.exception?.description ?? r.exceptionDetails.text)+'\nExpression: '+expression);return r.result.value}
  const waitFor=async expression=>{for(let i=0;i<100;i++){if(await evaluate(`Boolean(document.body && (${expression}))`))return;await sleep(100)}throw Error('condition timed out: '+expression+'\n'+await evaluate('document.body?.innerText.slice(0,1800) ?? "<body not ready>"'))}
  await send('Page.enable')
  const auth=JSON.stringify({state:{token:'isolated-local-only',user},version:0})
  await send('Page.addScriptToEvaluateOnNewDocument',{source:`localStorage.setItem('crm-auth',${JSON.stringify(auth)})`})
  // 页面导航存在 body 尚未建立的窗口；等待器必须继续等，不能因读取 innerText 抛错。
  await evaluate(`document.body.remove();setTimeout(()=>{const body=document.createElement('body');body.textContent='navigation-body-ready';document.documentElement.appendChild(body)},200)`)
  await waitFor(`document.body.innerText.includes('navigation-body-ready')`)
  console.log('BROWSER_NAVIGATION_BODY_WAIT=true')
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/workbench`})
  const checkbox=`document.querySelector('[aria-label="完成任务：确认保护测试任务"]')`
  await waitFor(checkbox)
  await evaluate(`${checkbox}.click()`)
  await waitFor(`document.body.innerText.includes('确认完成「确认保护测试任务」')`)
  assert.equal(completed,0,'opening must not submit')
  const popup=`document.querySelector('.semi-popconfirm')`
  await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.includes('取消')).click()`)
  await sleep(250)
  assert.equal(completed,0,'cancel must not submit')
  await evaluate(`${checkbox}.click()`)
  await waitFor(`document.body.innerText.includes('确认完成「确认保护测试任务」')`)
  const labels=await evaluate(`Array.from(${popup}.querySelectorAll('button')).map(b=>b.innerText)`)
  console.log('CONFIRM_BUTTONS='+JSON.stringify(labels))
  await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.trim()==='确定').click()`)
  for(let i=0;completed===0 && i<30;i++)await sleep(100)
  assert.equal(completed,1,'one confirm must submit once')
  console.log('BROWSER_OPEN_NO_SUBMIT=true')
  console.log('BROWSER_CANCEL_NO_SUBMIT=true')
  console.log('BROWSER_CONFIRM_EXACTLY_ONE_REQUEST=true')
  await sleep(250)
  await evaluate(`document.querySelector('.notice-trigger').click()`)
  const retryLink=`Array.from(document.querySelectorAll('.notice-panel a')).find(a=>a.innerText.trim()==='补投')`
  await waitFor(retryLink)
  await evaluate(`${retryLink}.click()`)
  await waitFor(`document.body.innerText.includes('确认补投这条通知')`)
  assert.equal(redispatched,0)
  assert.equal(markedRead,0,'opening nested confirmation must not mark read')
  assert.ok((await evaluate('location.pathname'))==='/workbench','must not navigate to notification object')
  await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.includes('取消')).click()`)
  await sleep(250)
  assert.equal(redispatched,0)
  await waitFor(retryLink)
  await evaluate(`${retryLink}.click()`)
  await waitFor(`document.body.innerText.includes('确认补投这条通知')`)
  await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.trim()==='确定').click()`)
  for(let i=0;redispatched===0 && i<30;i++)await sleep(100)
  assert.equal(redispatched,1)
  await waitFor(`document.body.innerText.includes('补投中…')`)
  await waitFor(`!document.body.innerText.includes('补投中…')`)
  assert.equal(markedRead,0)
  assert.equal(await evaluate('location.pathname'),'/workbench')
  console.log('BROWSER_NOTIFICATION_CANCEL_NO_SEND_AND_NO_NAVIGATION=true')
  console.log('BROWSER_NOTIFICATION_CONFIRM_ONCE_NO_PARENT_CLICK=true')
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/recycle-bin`})
  const restoreLink=`Array.from(document.querySelectorAll('a')).find(a=>a.innerText.trim()==='恢复')`
  await waitFor(restoreLink)
  await evaluate(`${restoreLink}.click()`)
  await waitFor(`document.body.innerText.includes('确认恢复这条线索')`)
  assert.equal(restored,0)
  await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.includes('取消')).click()`)
  await sleep(250)
  assert.equal(restored,0)
  await evaluate(`${restoreLink}.click()`)
  await waitFor(`document.body.innerText.includes('确认恢复这条线索')`)
  await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.trim()==='确定').click()`)
  for(let i=0;restored===0 && i<30;i++)await sleep(100)
  assert.equal(restored,1)
  console.log('BROWSER_RESTORE_CANCEL_NO_WRITE_CONFIRM_ONCE=true')
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/tasks?mine=false&status=active`})
  const finishLink=`Array.from(document.querySelectorAll('.semi-table-tbody a')).find(a=>a.innerText.trim()==='完成')`
  await waitFor(finishLink)
  const completedBefore=completed
  await evaluate(`${finishLink}.click()`)
  await waitFor(`document.body.innerText.includes('确认完成这条任务')`)
  assert.equal(completed,completedBefore)
  await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.includes('取消')).click()`)
  await sleep(250)
  assert.equal(completed,completedBefore)
  await evaluate(`${finishLink}.click()`)
  await waitFor(`document.body.innerText.includes('确认完成这条任务')`)
  await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.trim()==='确定').click()`)
  for(let i=0;completed===completedBefore && i<30;i++)await sleep(100)
  assert.equal(completed,completedBefore+1)
  assert.equal(completedBodies.at(-1).expected_updated_at,task.updated_at)
  console.log('BROWSER_TASK_CONFIRM_CORRECT_VERSION=true')
  await sleep(250)
  const selection=`document.querySelector('.semi-table-tbody .semi-checkbox input')`
  await waitFor(selection)
  await evaluate(`${selection}.click()`)
  const batchButton=`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.includes('批量完成') && !b.disabled)`
  await waitFor(batchButton)
  await evaluate(`${batchButton}.click()`)
  await waitFor(`document.body.innerText.includes('确认完成选中的 1 条任务')`)
  assert.equal(batchBodies.length,0)
  await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.includes('取消')).click()`)
  await sleep(250)
  assert.equal(batchBodies.length,0)
  await evaluate(`${batchButton}.click()`)
  await waitFor(`document.body.innerText.includes('确认完成选中的 1 条任务')`)
  await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.trim()==='确定').click()`)
  for(let i=0;batchBodies.length===0 && i<30;i++)await sleep(100)
  assert.equal(batchBodies.length,1)
  assert.deepEqual(batchBodies[0].task_ids,[7])
  console.log('BROWSER_BATCH_CANCEL_NO_WRITE_CONFIRM_EXACT_IDS=true')
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/leads`})
  const claimLead=`Array.from(document.querySelectorAll('.semi-table-tbody tr')).find(r=>r.innerText.includes('线索乙'))?.querySelector('a')`
  await waitFor(claimLead)
  await evaluate(`${claimLead}.click()`)
  await waitFor(`document.body.innerText.includes('确认领取线索「线索乙」')`)
  assert.equal(claimedLeads.length,0)
  await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.includes('取消')).click()`)
  await sleep(250)
  assert.equal(claimedLeads.length,0)
  await evaluate(`${claimLead}.click()`)
  await waitFor(`document.body.innerText.includes('确认领取线索「线索乙」')`)
  await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.trim()==='确定').click()`)
  for(let i=0;claimedLeads.length===0 && i<30;i++)await sleep(100)
  assert.deepEqual(claimedLeads,[32])
  await waitFor(`document.body.innerText.includes('领取中…')`)
  await waitFor(`!document.body.innerText.includes('领取中…')`)
  console.log('BROWSER_LEAD_CLAIM_CANCEL_NO_WRITE_CONFIRM_SECOND_ROW_ID=true')
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/customers/9`})
  const claimCustomer=`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='领取到我名下')`
  await waitFor(claimCustomer)
  await evaluate(`${claimCustomer}.click()`)
  await waitFor(`document.body.innerText.includes('确认领取客户「领取确认测试客户」')`)
  assert.equal(claimedCustomers.length,0)
  await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.includes('取消')).click()`)
  await sleep(250)
  assert.equal(claimedCustomers.length,0)
  await evaluate(`${claimCustomer}.click()`)
  await waitFor(`document.body.innerText.includes('确认领取客户「领取确认测试客户」')`)
  await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.trim()==='确定').click()`)
  for(let i=0;claimedCustomers.length===0 && i<30;i++)await sleep(100)
  assert.deepEqual(claimedCustomers,[9])
  console.log('BROWSER_CUSTOMER_CLAIM_CANCEL_NO_WRITE_CONFIRM_CORRECT_ID=true')
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/approvals`})
  const approveLink=`Array.from(document.querySelectorAll('.semi-table-tbody tr')).find(r=>r.innerText.includes('隔离审批-52')) && Array.from(Array.from(document.querySelectorAll('.semi-table-tbody tr')).find(r=>r.innerText.includes('隔离审批-52')).querySelectorAll('a')).find(a=>a.innerText.trim()==='通过')`
  await waitFor(approveLink)
  await evaluate(`${approveLink}.click()`)
  await waitFor(`document.body.innerText.includes('通过这份报价的审批')`)
  assert.equal(approvalsApproved.length,0)
  await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.includes('取消')).click()`)
  await sleep(250)
  assert.equal(approvalsApproved.length,0)
  await evaluate(`${approveLink}.click()`)
  await waitFor(`document.body.innerText.includes('通过这份报价的审批')`)
  await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.trim()==='确定').click()`)
  for(let i=0;approvalsApproved.length===0 && i<30;i++)await sleep(100)
  assert.deepEqual(approvalsApproved,[52])
  console.log('BROWSER_APPROVE_CANCEL_NO_WRITE_CONFIRM_CORRECT_ID=true')
  await sleep(250)
  const rejectLink=`Array.from(Array.from(document.querySelectorAll('.semi-table-tbody tr')).find(r=>r.innerText.includes('隔离审批-51')).querySelectorAll('a')).find(a=>a.innerText.trim()==='拒绝')`
  await evaluate(`${rejectLink}.click()`)
  const reasonInput=`document.querySelector('input[placeholder="拒绝原因，例如：低于成本，不可让价"]')`
  await waitFor(reasonInput)
  assert.equal(approvalsRejected.length,0)
  await evaluate(`Array.from(document.querySelectorAll('.semi-modal-content button')).find(b=>b.innerText.trim()==='取消').click()`)
  await sleep(250)
  assert.equal(approvalsRejected.length,0)
  await evaluate(`${rejectLink}.click()`)
  await waitFor(reasonInput)
  await evaluate(`{const input=${reasonInput};Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'隔离回归拒绝原因');input.dispatchEvent(new Event('input',{bubbles:true}))}`)
  await evaluate(`Array.from(document.querySelectorAll('.semi-modal-content button')).find(b=>b.innerText.trim()==='确认拒绝').click()`)
  for(let i=0;approvalsRejected.length===0 && i<30;i++)await sleep(100)
  assert.deepEqual(approvalsRejected,[{id:51,comment:'隔离回归拒绝原因'}])
  console.log('BROWSER_REJECT_SINGLE_FORM_CANCEL_NO_WRITE_CONFIRM_REASON=true')
  for(const [id,label,title,action] of [
    [61,'批准','确认批准这张打样申请','approve'],
    [62,'拒绝','确认拒绝这张打样申请','approve'],
    [63,'重新提交审批','确认重新提交这张打样申请','resubmit'],
    [64,'登记寄样','确认登记寄样','ship'],
    [65,'登记签收','确认登记客户已签收','sign'],
    [66,'开新修订版（改车间依据）','确认开新修订版','revise'],
    [67,'改判为批准','确认改判为批准','approve'],
  ]) {
    await send('Page.navigate',{url:`http://127.0.0.1:${port}/samples/${id}`})
    const button=`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()===${JSON.stringify(label)})`
    await waitFor(button)
    if(id===62) await evaluate(`{const input=document.querySelector('input[placeholder="拒绝原因（拒绝时必填）"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'隔离样品拒绝原因');input.dispatchEvent(new Event('input',{bubbles:true}))}`)
    const before=sampleRequests.length
    await evaluate(`${button}.click()`)
    await waitFor(`document.body.innerText.includes(${JSON.stringify(title)})`)
    assert.equal(sampleRequests.length,before)
    await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.includes('取消')).click()`)
    await sleep(250)
    assert.equal(sampleRequests.length,before)
    await evaluate(`${button}.click()`)
    await waitFor(`document.body.innerText.includes(${JSON.stringify(title)})`)
    await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.trim()==='确定').click()`)
    for(let i=0;sampleRequests.length===before && i<30;i++)await sleep(100)
    assert.equal(sampleRequests.length,before+1)
    assert.equal(sampleRequests.at(-1).id,id)
    assert.equal(sampleRequests.at(-1).action,action)
    if(action==='approve') assert.equal(sampleRequests.at(-1).body.approved,id!==62)
    if(id===62) assert.equal(sampleRequests.at(-1).body.reject_reason,'隔离样品拒绝原因')
    console.log(`BROWSER_SAMPLE_${id}_${action}_CANCEL_NO_WRITE_CONFIRM_CORRECT_ID=true`)
  }
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/wecom`})
  for(const [label,endpoint] of [
    ['同步部门','sync-departments'],['同步成员','sync-users'],
    ['同步外部联系人','sync-external-contacts'],['同步跟进关系','sync-follow-relations'],
  ]) {
    const button=`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()===${JSON.stringify(label)})`
    await waitFor(button)
    const before=wecomRequests.length
    await evaluate(`${button}.click()`)
    await waitFor(`document.body.innerText.includes(${JSON.stringify('确认'+label+'？')})`)
    assert.equal(wecomRequests.length,before,'opening sync confirmation must not submit')
    await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.trim()==='取消').click()`)
    await sleep(250)
    assert.equal(wecomRequests.length,before,'cancel sync must not submit')
    await evaluate(`${button}.click()`)
    await waitFor(`document.body.innerText.includes(${JSON.stringify('确认'+label+'？')})`)
    await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.trim()==='确定').click()`)
    for(let i=0;wecomRequests.length===before && i<30;i++)await sleep(100)
    assert.equal(wecomRequests.length,before+1)
    assert.equal(wecomRequests.at(-1),endpoint,'sync must call its own endpoint')
    await waitFor(`!${popup}`)
    console.log(`BROWSER_WECOM_${endpoint}_CANCEL_NO_WRITE_CONFIRM_CORRECT_ENDPOINT=true`)
  }
  const bindButton=`Array.from(document.querySelectorAll('button')).filter(b=>b.innerText.trim()==='关联已有客户')[1]`
  await waitFor(bindButton)
  await evaluate(`${bindButton}.click()`)
  await waitFor(`document.body.innerText.includes('确认关联客户「隔离匹配客户乙」')`)
  assert.equal(wecomBindings.length,0)
  await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.trim()==='取消').click()`)
  await sleep(250)
  assert.equal(wecomBindings.length,0)
  await evaluate(`${bindButton}.click()`)
  await waitFor(`document.body.innerText.includes('确认关联客户「隔离匹配客户乙」')`)
  await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.trim()==='确定').click()`)
  for(let i=0;wecomBindings.length===0 && i<30;i++)await sleep(100)
  assert.deepEqual(wecomBindings,[{contact_id:92,customer_id:94}])
  console.log('BROWSER_WECOM_BIND_CANCEL_NO_WRITE_CONFIRM_SECOND_CANDIDATE_ID=true')
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/quotes/121?version=131`})
  const withdraw=`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='撤回审批')`
  await waitFor(withdraw)
  await evaluate(`${withdraw}.click()`)
  await waitFor(`document.body.innerText.includes('确认撤回当前版本的报价审批')`)
  assert.equal(quoteWithdrawals.length,0)
  await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.trim()==='取消').click()`)
  await sleep(250)
  assert.equal(quoteWithdrawals.length,0)
  await evaluate(`${withdraw}.click()`)
  await waitFor(`document.body.innerText.includes('确认撤回当前版本的报价审批')`)
  await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.trim()==='确定').click()`)
  for(let i=0;quoteWithdrawals.length===0 && i<30;i++)await sleep(100)
  assert.deepEqual(quoteWithdrawals,[131])
  console.log('BROWSER_QUOTE_WITHDRAW_CANCEL_NO_WRITE_CONFIRM_CURRENT_VERSION=true')
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/quotes/121?version=130`})
  await waitFor(`location.search==='?version=130' && document.readyState==='complete' && document.body.innerText.includes('ISO-QUOTE-121')`)
  assert.equal(await evaluate(`Boolean(${withdraw})`),false,'historical version must not offer withdrawal')
  assert.deepEqual(quoteWithdrawals,[131])
  console.log('BROWSER_QUOTE_HISTORICAL_VERSION_NO_WITHDRAW=true')
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/recycle-bin`})
  for(const [tab,name,title,endpoint,modal] of [
    ['产品','隔离恢复产品','确认恢复产品及随产品删除的 SKU','products/101',false],
    ['产品','ISO-SKU-102','确认恢复这条 SKU','skus/102',false],
    ['客户','隔离恢复客户','确认恢复客户「隔离恢复客户」','customers/103',true],
    ['样品','隔离恢复样品客户','确认恢复这张样品申请','samples/104',false],
    ['合同','ISO-CONTRACT-105','确认恢复这份合同','contract-documents/105',false],
  ]) {
    const tabButton=`Array.from(document.querySelectorAll('[role="tab"]')).find(t=>t.innerText.trim()===${JSON.stringify(tab)})`
    await waitFor(tabButton)
    await evaluate(`${tabButton}.click()`)
    const restore=`Array.from(document.querySelectorAll('.semi-table-tbody tr')).find(r=>r.innerText.includes(${JSON.stringify(name)}))?.querySelector('a:last-child')`
    await waitFor(restore)
    assert.equal(await evaluate(`${restore}.innerText.trim()`),'恢复')
    if(tab==='产品') assert.equal(await evaluate(`Array.from(document.querySelectorAll('.semi-table-tbody tr')).find(r=>r.innerText.includes('ISO-SKU-107')).querySelectorAll('a').length`),0,'SKU whose product is deleted must have no restore action')
    if(tab==='客户') assert.equal(await evaluate(`Array.from(document.querySelectorAll('.semi-table-tbody tr')).find(r=>r.innerText.includes('被合并客户')).innerText.includes('恢复')`),false,'merged customer must have no restore action')
    const before=recycleRequests.length
    const container=modal?`document.querySelector('.semi-modal-confirm')`:popup
    await evaluate(`${restore}.click()`)
    await waitFor(`document.body.innerText.includes(${JSON.stringify(title)})`)
    assert.equal(recycleRequests.length,before)
    await evaluate(`Array.from(${container}.querySelectorAll('button')).find(b=>b.innerText.trim()==='取消').click()`)
    await sleep(250)
    assert.equal(recycleRequests.length,before)
    await evaluate(`${restore}.click()`)
    await waitFor(`document.body.innerText.includes(${JSON.stringify(title)})`)
    await evaluate(`Array.from(${container}.querySelectorAll('button')).find(b=>b.innerText.trim()==='确定').click()`)
    for(let i=0;recycleRequests.length===before && i<30;i++)await sleep(100)
    assert.equal(recycleRequests.length,before+1)
    assert.equal(recycleRequests.at(-1).path,`/api/v1/${endpoint}/restore`)
    if(modal) assert.deepEqual(recycleRequests.at(-1).body,{owner_id:null})
    await waitFor(`!${container}`)
    console.log(`BROWSER_RECYCLE_${endpoint}_CANCEL_NO_WRITE_CONFIRM_CORRECT_ID=true`)
  }
  await evaluate(`Array.from(document.querySelectorAll('[role="tab"]')).find(t=>t.innerText.trim()==='客户').click()`)
  const reassignRestore=`Array.from(document.querySelectorAll('.semi-table-tbody tr')).find(r=>r.innerText.includes('待重新分配客户'))?.querySelector('a:last-child')`
  await waitFor(reassignRestore)
  const beforeReassign=recycleRequests.length
  await evaluate(`${reassignRestore}.click()`)
  await waitFor(`document.body.innerText.includes('指定新负责人')`)
  const ownerModal=`document.querySelector('.semi-modal-content')`
  const ownerSubmit=`Array.from(${ownerModal}.querySelectorAll('button')).find(b=>b.innerText.trim()==='恢复并交给这位')`
  await evaluate(`${ownerSubmit}.click()`)
  await waitFor(`document.body.innerText.includes('请选择一位在职的新负责人')`)
  assert.equal(recycleRequests.length,beforeReassign,'missing replacement owner must not submit')
  const ownerSelect=`${ownerModal}.querySelector('.semi-select')`
  await evaluate(`${ownerSelect}.click()`)
  const newOwner=`Array.from(document.querySelectorAll('.semi-select-option')).find(o=>o.innerText.includes('在职员工乙（owner1）'))`
  await waitFor(newOwner)
  await evaluate(`${newOwner}.click()`)
  await waitFor(`${ownerSelect}.innerText.includes('在职员工乙')`)
  await evaluate(`Array.from(${ownerModal}.querySelectorAll('button')).find(b=>b.innerText.trim()==='取消').click()`)
  await sleep(250)
  assert.equal(recycleRequests.length,beforeReassign,'cancel after selecting replacement owner must not submit')
  await evaluate(`${reassignRestore}.click()`)
  await waitFor(`document.body.innerText.includes('指定新负责人')`)
  assert.equal(await evaluate(`${ownerModal}.innerText.includes('在职员工乙')`),false,'reopening must clear the cancelled selection')
  await evaluate(`${ownerSelect}.click()`)
  await waitFor(newOwner)
  await evaluate(`${newOwner}.click()`)
  await waitFor(`${ownerSelect}.innerText.includes('在职员工乙')`)
  const ownerInput=`${ownerSelect}.querySelector('input')`
  const searchOwner=async keyword=>{
    await evaluate(`(()=>{const input=${ownerInput};Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,${JSON.stringify(keyword)});input.dispatchEvent(new Event('input',{bubbles:true}))})()`)
    for(let i=0;ownerRequests.at(-1)?.keyword!==keyword&&i<40;i++)await sleep(100)
    await waitFor(`Array.from(document.querySelectorAll('.semi-select-option')).some(o=>o.textContent.includes('末页在职员工（last.owner）'))`)
    assert.equal(ownerRequests.at(-1).keyword,keyword)
    assert.equal(ownerRequests.at(-1).page,'1')
    assert.equal(ownerRequests.at(-1).page_size,'20')
    assert.equal(ownerRequests.at(-1).status,'active')
  }
  assert.ok(ownerRequests.every(r=>r.page==='1'&&r.page_size==='20'),'opening must not fetch every employee')
  await evaluate(`${ownerSelect}.click()`)
  await searchOwner('末页在职员工')
  assert.equal(await evaluate(`document.querySelector('.semi-select-option-selected')?.textContent.includes('在职员工乙')`),true,'search and pagination must preserve selected option')
  await searchOwner('last.owner')
  await evaluate(`(()=>{const input=${ownerInput};Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'');input.dispatchEvent(new Event('input',{bubbles:true}))})()`)
  const loadMore=`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='加载更多')`
  for(let i=0;ownerRequests.at(-1)?.keyword!==''&&i<40;i++)await sleep(100)
  await waitFor(loadMore)
  await evaluate(`${loadMore}.click()`)
  await waitFor(`Array.from(document.querySelectorAll('.semi-select-option')).some(o=>o.textContent.includes('分页员工20（owner20）'))`)
  assert.equal(ownerRequests.at(-1).page,'2')
  assert.equal(ownerRequests.at(-1).keyword,'')
  assert.equal(await evaluate(`document.querySelector('.semi-select-option-selected')?.textContent.includes('在职员工乙')`),true,'search and pagination must preserve selected option')
  await searchOwner('末页在职员工')
  await evaluate(`Array.from(document.querySelectorAll('.semi-select-option')).find(o=>o.textContent.includes('末页在职员工（last.owner）')).click()`)
  await waitFor(`${ownerSelect}.innerText.includes('末页在职员工')`)
  console.log('BROWSER_RECYCLE_OWNER_NAME_USERNAME_SEARCH_ON_DEMAND_PAGING_PRESERVES_SELECTION=true')
  await evaluate(`${ownerSubmit}.click()`)
  for(let i=0;recycleRequests.length===beforeReassign && i<30;i++)await sleep(100)
  assert.equal(recycleRequests.length,beforeReassign+1,await evaluate('document.body.innerText.slice(-2400)'))
  assert.deepEqual(recycleRequests.at(-1),{path:'/api/v1/customers/106/restore',body:{owner_id:311}})
  console.log('BROWSER_RECYCLE_INACTIVE_OWNER_CANCEL_AND_REQUIRED_SELECTION_CORRECT_ID=true')
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/settings?tab=rules`})
  for(const [label,decision,title] of [
    ['批量批准','approve','批准选中的 2 条回收候选'],
    ['批量暂缓','defer','暂缓选中的 2 条回收候选'],
    ['批量驳回','reject','驳回选中的 2 条回收候选'],
  ]) {
    for(const id of [141,143]) {
      const check=`Array.from(document.querySelectorAll('.semi-table-tbody tr')).find(r=>r.innerText.includes('隔离回收候选${id}'))?.querySelector('input[type="checkbox"]')`
      await waitFor(check)
      await evaluate(`${check}.click()`)
    }
    await waitFor(`document.body.innerText.includes('已选 2 条')`)
    const button=`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()===${JSON.stringify(label)})`
    const before=settingsRequests.length
    await evaluate(`${button}.click()`)
    await waitFor(`document.body.innerText.includes(${JSON.stringify(title)})`)
    assert.equal(settingsRequests.length,before)
    await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.trim()==='取消').click()`)
    await sleep(250)
    assert.equal(settingsRequests.length,before)
    await evaluate(`${button}.click()`)
    await waitFor(`document.body.innerText.includes(${JSON.stringify(title)})`)
    await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.trim()==='确定').click()`)
    for(let i=0;settingsRequests.length===before && i<30;i++)await sleep(100)
    assert.equal(settingsRequests.length,before+1)
    assert.deepEqual(settingsRequests.at(-1),{path:'/api/v1/public-pool/recycle-candidates/batch-decide',body:{candidate_ids:[141,143],decision,early:false}})
    await waitFor(`document.body.innerText.includes('已选 0 条')`)
    await waitFor(`!${popup}`)
    console.log(`BROWSER_SETTINGS_BATCH_${decision}_CANCEL_NO_WRITE_CONFIRM_EXACT_IDS=true`)
  }
  for(const [label,title,path] of [
    ['扫描并生成回收预告','确认扫描并生成回收预告','/api/v1/public-pool/run-recycle'],
    ['立即执行一次','确认立即执行自动任务规则','/api/v1/tasks/run-auto-rules'],
  ]) {
    const button=`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()===${JSON.stringify(label)})`
    await waitFor(button)
    const before=settingsRequests.length
    await evaluate(`${button}.click()`)
    await waitFor(`document.body.innerText.includes(${JSON.stringify(title)})`)
    assert.equal(settingsRequests.length,before)
    await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.trim()==='取消').click()`)
    await sleep(250)
    assert.equal(settingsRequests.length,before)
    await evaluate(`${button}.click()`)
    await waitFor(`document.body.innerText.includes(${JSON.stringify(title)})`)
    await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.trim()==='确定').click()`)
    for(let i=0;settingsRequests.length===before && i<30;i++)await sleep(100)
    assert.equal(settingsRequests.length,before+1)
    assert.equal(settingsRequests.at(-1).path,path)
    await waitFor(`!${popup}`)
    console.log(`BROWSER_SETTINGS_${path.split('/').at(-1)}_CANCEL_NO_WRITE_CONFIRM_ONCE=true`)
  }
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/customers`})
  for (const mode of ['create','batch']) {
    const buttonLabel=mode==='create'?'新建客户':'批量转移负责人'
    if (mode==='batch') {
      const checkbox=`document.querySelector('.semi-table-tbody input[type=checkbox]')`
      await waitFor(checkbox)
      await evaluate(`${checkbox}.click()`)
    }
    const button=`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()===${JSON.stringify(buttonLabel)})`
    await waitFor(button)
    await evaluate(`${button}.click()`)
    const modal=`document.querySelector('.semi-modal-content')`
    const picker=mode==='create'?`${modal}.querySelector('.owner-picker')`:`${modal}.querySelector('.semi-select')`
    await waitFor(picker)
    await evaluate(`${picker}.click()`)
    const input=`${picker}.querySelector('input')`
    const search=async keyword=>{
      const before=ownerRequests.length
      await evaluate(`(()=>{const el=${input};Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,${JSON.stringify(keyword)});el.dispatchEvent(new Event('input',{bubbles:true}))})()`)
      for(let i=0;!(ownerRequests.length>before&&ownerRequests.at(-1).keyword===keyword)&&i<40;i++)await sleep(100)
      assert.equal(ownerRequests.at(-1).keyword,keyword)
      assert.equal(ownerRequests.at(-1).page_size,'20')
    }
    await waitFor(`Array.from(document.querySelectorAll('button')).some(b=>b.innerText.trim()==='加载更多')`)
    await evaluate(`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='加载更多').click()`)
    await waitFor(`Array.from(document.querySelectorAll('.semi-select-option')).some(o=>o.textContent.includes('分页员工20'))`)
    assert.equal(ownerRequests.at(-1).page,'2')
    await search('末页在职员工')
    const lastOption=`Array.from(document.querySelectorAll('.semi-select-option')).find(o=>o.textContent.includes('末页在职员工（last.owner'))`
    await waitFor(lastOption)
    await evaluate(`${lastOption}.click()`)
    await waitFor(`${picker}.innerText.includes('末页在职员工')`)
    await evaluate(`${picker}.click()`)
    await search('owner1')
    await waitFor(`Array.from(document.querySelectorAll('.semi-select-option')).some(o=>o.textContent.includes('在职员工乙'))`)
    assert.equal(await evaluate(`document.querySelector('.semi-select-option-selected')?.textContent.includes('末页在职员工')`),true,'changing keyword must preserve selected owner')
    await search('last.owner')
    await waitFor(lastOption)
    await evaluate(`${lastOption}.click()`)
    await waitFor(`${picker}.innerText.includes('末页在职员工')`)
    if(mode==='create') {
      await evaluate(`(()=>{const el=${modal}.querySelector('input[placeholder^="公司全称"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,'隔离按需选人客户');el.dispatchEvent(new Event('input',{bubbles:true}))})()`)
      await waitFor(`${modal}.querySelector('input[placeholder^="公司全称"]').value==='隔离按需选人客户'`)
    }
    const before=customerOwnerWrites.length
    await evaluate(`Array.from(${modal}.querySelectorAll('button')).find(b=>b.innerText.trim()===${JSON.stringify(mode==='create'?'创建':'确认转移')}).click()`)
    for(let i=0;customerOwnerWrites.length===before&&i<40;i++)await sleep(100)
    assert.equal(customerOwnerWrites.length,before+1)
    assert.equal(customerOwnerWrites.at(-1).body.owner_id,311)
    if(mode==='batch')assert.deepEqual(customerOwnerWrites.at(-1).body.customer_ids,[9])
    else assert.ok(customerOwnerWrites.at(-1).body.request_key,'create keeps idempotency key')
    await waitFor(`!${modal}`)
    console.log(`BROWSER_CUSTOMER_${mode}_OWNER_REMOTE_NAME_ACCOUNT_PAGING_PRESERVE_SUBMIT_ID=true`)
  }
} finally {
  socket?.close();browser?.kill('SIGTERM');await sleep(500);server.closeAllConnections();server.close();rmSync(profile,{recursive:true,force:true,maxRetries:5,retryDelay:200})
}
