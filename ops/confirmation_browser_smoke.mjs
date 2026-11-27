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
const task={id:7,customer_id:2,customer_name:'隔离任务客户',title:'确认保护测试任务',status:'pending',status_label:'待处理',owner_id:1,priority:'normal',due_at:new Date().toISOString(),updated_at:new Date().toISOString()}
let completed=0, redispatched=0, markedRead=0, restored=0, browser, socket
const completedBodies=[], batchBodies=[]
const handoverBodies=[]
const inquiryVersions=[
  {id:401,title:'隔离询价旧版',version:1,is_superseded:true,status:'open',status_label:'待评估'},
  {id:402,title:'隔离询价新版',version:2,root_id:401,status:'open',status_label:'待评估'},
  {id:403,title:'隔离归档询价',version:1,status:'archived',status_label:'已归档'},
  {id:404,title:'隔离归档有原报价',version:1,status:'archived',status_label:'已归档',linked_quotes:[{id:121,quote_no:'EXACT-QUOTE-121'}]},
]
const caseFixture={id:405,title:'隔离培训案例',status:'published',status_label:'已发布',author_id:2}
let skuDiffReads=0
const skuDiffWrites=[]
const claimedLeads=[], claimedCustomers=[]
const approvalsApproved=[], approvalsRejected=[]
const sampleRequests=[]
const wecomRequests=[]
let rejectWecomSync=false
const failedWecomJobs=[]
const wecomBindings=[]
const recycleRequests=[]
const quoteWithdrawals=[]
const quoteDeletions=[]
let rejectQuoteDeletion=false
let quoteCostProblems=[]
let costDraft=false
let quoteItems=[]
let exactQuote=false
let failReceivables=false
let financialRows=false
let financialPlansEmpty=false
const financialPayments=[]
const exactOrder={id:991,order_no:'EXACT-MONEY-991',customer_id:9,currency:'CNY',total_amount:99999999999999.98,total_amount_exact:'99999999999999.99',received_amount:99999999999999.98,received_amount_exact:'99999999999999.98',unreceived_amount:0.01,unreceived_amount_exact:'0.01',status:'pending',status_label:'待生产'}
const exactPlan={id:992,order_id:991,order_no:exactOrder.order_no,plan_name:'精确应收',due_date:'2027-01-01',currency:'CNY',amount:exactOrder.total_amount,amount_exact:exactOrder.total_amount_exact,received_amount:0,received_amount_exact:'0.00',remaining_amount:exactOrder.total_amount,remaining_amount_exact:exactOrder.total_amount_exact,status:'pending',status_label:'待回款'}
const exactPayment={id:993,order_id:991,order_no:exactOrder.order_no,currency:'CNY',received_date:'2026-10-27',received_amount:exactOrder.total_amount,received_amount_exact:exactOrder.total_amount_exact,status:'pending',status_label:'待确认'}
const costRefreshes=[], costPageRequests=[]
const skuCreates=[]
const settingsRequests=[]
const apiRequests=[]
const customerOwnerWrites=[]
const ownerRequests=[]
const ownerUsers=Array.from({length:201},(_,i)=>({id:111+i,name:i===0?'在职员工甲':i===1?'在职员工乙':i===200?'末页在职员工':`分页员工${i}`,username:i===200?'last.owner':`owner${i}`,status:'active'}))
const quoteVersion=id=>({id,quote_id:121,version_no:id===131?2:1,subtotal_amount:100,charge_amount:0,discount_amount:0,total_amount:100,logistics_amount:0,other_charge_amount:0,pricing_basis:'actual_pass_through',approval_status:costDraft?'not_submitted':'pending',approval_required:true,created_at:new Date().toISOString(),...(exactQuote?{total_amount:99999999999999.98,total_amount_exact:'99999999999999.99',approval_status:'approved',accepted_at:new Date().toISOString(),sent_at:new Date().toISOString()}:{} )})
const recycleRecord=(id,name,extra={})=>({id,name,status_label:'已删除',deleted_by_pending:true,deleted_at:new Date().toISOString(),...extra})
const wecomContact={id:92,external_userid:'isolated-contact',name:'隔离企微联系人',corp_name:'隔离公司',normalize_status:'pending',normalize_status_label:'待确认',followers:[]}
const sampleStatuses={61:'pending',62:'pending',63:'rejected',64:'approved',65:'shipped',66:'shipped',67:'rejected'}
// source_context.dingtalk_oa.submitted 决定详情页「下一步」按钮文案（sampleNextStep）：
// 61 未送审→「填写或提交 OA 申请」，62 已送审→「查看 OA 进度」；63/67 被拒走「查看 OA 审批意见」（不读这字段）。
const sampleFixture=id=>({id,status:sampleStatuses[id]??'pending',status_label:'隔离样品状态',customer_name:'隔离样品客户',items:[],shipments:[],confirm_status:'pending',made_at:[64,65,66].includes(id)?'2026-11-18T08:00:00+08:00':null,
  // 2026-11-26 起所有样品寄样前都要主管批准；64 要验证「登记寄样」确认弹窗，
  // 夹具必须带上已批准的寄样审批，否则详情页只显示「提交寄样申请」。
  ship_approval:id===64?{status:'approved',status_label:'已批准',recipient:'隔离收件人 13800000000',payer:'company',payer_label:'公司承担',carrier:'隔离物流',shipping_fee:0,shipping_fee_exact:'0'}:null,
  source_context:[61,62].includes(id)?{dingtalk_oa:{submitted:id===62}}:null})
// GET /samples/{id}/dingtalk 夹具，形状照 SampleDingTalkModal 的 Options 接口（字段空表=免必填校验，
// detail_files 与 draft.sample_detail_file_ids 对上=附件校验能过，提交按钮才点得动）。
// 62 已送审在审：pending+self_oa+instance_id → 弹窗出「撤回」按钮（用例不点）；无重提/重试动作 → 无提交按钮。
// 63/67 被拒可重提：allowed_actions 含 resubmit → 弹窗出「修改后重新提交」。
const sampleOaFixture=id=>({
  configured:true,enabled:true,item_count:0,fields:[],
  detail_files:[{id:77,file_name:'隔离样品明细表.xlsx'}],
  draft:{mode:'standalone',fields:{},sample_detail_file_ids:[77],submitted:[62,63,67].includes(id)},
  candidates:[],
  records:id===62?[{id:862,inquiry_id:0,inquiry_version:0,oa_type:'sample',provider:'self_oa',instance_id:'ISO-OA-62',status:'pending',status_label:'审批中',submit_round:1,attempt_count:1,allowed_actions:[]}]
    :[63,67].includes(id)?[{id:800+id,inquiry_id:0,inquiry_version:0,oa_type:'sample',provider:'self_oa',instance_id:`ISO-OA-${id}`,status:'rejected',status_label:'已拒绝',submit_round:1,attempt_count:1,allowed_actions:['resubmit']}]
    :[],
})
const publicCustomer={id:9,name:'领取确认测试客户',pool_status:'public',status:'active',tags:[],owner_id:null}
let analyticsExact=false
const analyticsTargetWrites=[]
let rejectStaleTarget=false
const analyticsFixture=JSON.parse(readFileSync(new URL('./fixtures/analytics-whole-yuan.json',import.meta.url),'utf8'))
const financeSummaryFixture=JSON.parse(readFileSync(new URL('./fixtures/order-finance-exact.json',import.meta.url),'utf8'))
const shipmentBodies=[];const savedShipments=new Map();let firstShipmentFail=true;let shipmentLarge=false
const draftPrecisionBodies=[]
let draftPrecision={id:993,customer_id:2,status:'draft',revision:1,currency:'CNY',source_context:{type:'inquiry',id:1,no:'ISO-DRAFT-PRECISION',version:1},items:[{id:501,source_item_id:1,name:'隔离草稿精确明细',quantity:9999999999999.998,quantity_exact:'9999999999999.999',unit_price:0.0001,unit_price_exact:'0.0001',source_snapshot:{source_item_id:1,original_quantity:'9999999999999.999',specification:null,remark:null},specification:null,remark:null}],remark:null,delivery_date:null,payment_terms:null}
const sourceCreateBodies={order:[],sample:[]}
const sourcePrecision={source:{type:'quote_version',id:121,quote_id:121,no:'ISO-SOURCE-PRECISION',version:1,is_historical:false},customer_id:2,customer_name:'隔离来源客户',currency:'CNY',items:[{source_item_id:23,name:'隔离来源精确明细',original_quantity:'9999999999999.999',unit_price:'0.0001',specification:null,remark:null}]}
const server=createServer(async (req,res)=>{
  const url=new URL(req.url,'http://localhost')
  const path=url.pathname
  if(path==='/api/v1/order-drafts/source'||path==='/api/v1/samples/source') {
    res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({code:0,message:'ok',data:sourcePrecision}));return
  }
  if((path==='/api/v1/order-drafts'||path==='/api/v1/samples/from-source')&&req.method==='POST') {
    const mode=path.endsWith('from-source')?'sample':'order'
    let raw='';for await(const chunk of req)raw+=chunk;sourceCreateBodies[mode].push(JSON.parse(raw))
    res.writeHead(500,{'Content-Type':'application/json'});res.end(JSON.stringify({code:50001,message:'隔离模拟来源创建失败',data:null}));return
  }
  if(path==='/api/v1/order-drafts/993') {
    if(req.method==='PATCH') {
      let raw='';for await(const c of req)raw+=c;const body=JSON.parse(raw);draftPrecisionBodies.push(body)
      draftPrecision={...draftPrecision,...body,revision:draftPrecision.revision+1,items:body.items.map((row,index)=>({...draftPrecision.items[index],...row,quantity_exact:row.quantity,unit_price_exact:row.unit_price}))}
    }
    res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({code:0,message:'ok',data:draftPrecision}));return
  }
  if(path==='/api/v1/orders/991/shipments') {
    if(req.method==='POST') {
      let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);shipmentBodies.push(body)
      if(!savedShipments.has(body.request_key))savedShipments.set(body.request_key,{batch_id:700+savedShipments.size,batch_no:savedShipments.size+1})
      if(firstShipmentFail){firstShipmentFail=false;res.writeHead(500,{'Content-Type':'application/json'});res.end(JSON.stringify({code:50001,message:'隔离模拟：批次已保存但回执异常',data:null}));return}
      res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({code:0,message:'ok',data:savedShipments.get(body.request_key)}));return
    }
    const data={items:[{order_item_id:77,sku:'隔离发货型号',ordered:100,planned:0,shipped:0,unplanned:100,remaining:100,ordered_exact:'100.000',shipped_exact:'0.000',remaining_exact:'100.000',unplanned_exact:'100.000'}],batches:[],summary:{ordered:100,planned:0,shipped:0,remaining:100,all_shipped:false,ordered_exact:'100.000',shipped_exact:'0.000',remaining_exact:'100.000'}}
    if(shipmentLarge) {
      for(const target of [data.items[0],data.summary]) for(const field of ['ordered','remaining','unplanned']) {target[field]=9999999999999.998;target[field+'_exact']='9999999999999.999'}
      data.batches=[{id:999,batch_no:9,status:'cancelled',status_label:'已取消',planned_date:null,actual_ship_date:null,logistics_company:null,tracking_no:null,deviation_days:null,late:false,items:[{order_item_id:77,sku:'隔离旧批次',planned_qty:9999999999999.998,planned_qty_exact:'9999999999999.999',shipped_qty:0,shipped_qty_exact:'0.000'}]}]
    }
    res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({code:0,message:'ok',data}));return
  }
  if(analyticsExact && path==='/api/v1/sales-targets/upsert' && req.method==='POST') {
    let raw='';for await(const chunk of req) raw+=chunk
    const payload=JSON.parse(raw)
    analyticsTargetWrites.push(payload)
    if(rejectStaleTarget){
      res.writeHead(409,{'Content-Type':'application/json'})
      res.end(JSON.stringify({code:40902,message:'这条目标刚被别人改过，请刷新后重新编辑（不要覆盖别人的改动）',data:null}));return
    }
    const targetId=payload.department_id===analyticsFixture.team_target.department_id ? analyticsFixture.team_target.target_id : analyticsFixture.money_target.target_id
    res.writeHead(200,{'Content-Type':'application/json'})
    res.end(JSON.stringify({code:0,data:{target_id:targetId}}));return
  }
  if(analyticsExact && analyticsFixture.responses[path]) {
    res.writeHead(200,{'Content-Type':'application/json'})
    res.end(JSON.stringify(analyticsFixture.responses[path]));return
  }
  if(failReceivables && path==='/api/v1/analytics/receivables') {
    res.writeHead(500,{'Content-Type':'application/json'})
    res.end(JSON.stringify({code:50001,message:'隔离模拟读取失败',data:null}));return
  }
  if(path.startsWith('/api/')) {
    apiRequests.push({path,method:req.method})
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
    else if(path==='/api/v1/payments' && req.method==='POST') {financialPayments.push(body);data=exactPayment}
    else if(path==='/api/v1/orders/994/shipments') data={items:[],batches:[],summary:{ordered:1000,planned:0,shipped:0,remaining:1000,all_shipped:false,ordered_exact:'1000.000',planned_exact:'0.000',shipped_exact:'0.000',remaining_exact:'1000.000',unplanned_exact:'1000.000'}}
    else if(path==='/api/v1/orders/994') data={...exactOrder,id:994,order_no:'FOUR-DECIMAL-994',total_amount:33333.3,total_amount_exact:'33333.30',received_amount:0,received_amount_exact:'0.00',unreceived_amount:33333.3,unreceived_amount_exact:'33333.30'}
    else if(path==='/api/v1/orders/994/items') data=[{id:995,order_id:994,sku_id:1,sku_code:'FOUR-DECIMAL-ITEM',quantity:1000,unit_price:33.3333,unit_price_exact:'33.3333',amount:33333.3,amount_exact:'33333.30'}]
    else if(path==='/api/v1/orders/991/finance-summary') data=financeSummaryFixture
    else if(path==='/api/v1/orders/991') data=exactOrder
    else if(path==='/api/v1/orders/991/receivables') data=financialPlansEmpty?[]:[exactPlan]
    else if(['/api/v1/orders','/api/v1/receivables','/api/v1/payments'].includes(path)) data={items:financialRows?[path.endsWith('/orders')?exactOrder:path.endsWith('/receivables')?exactPlan:exactPayment]:[],total:financialRows?1:0}
    else if(path.endsWith('/auth/me')) data=user
    else if(path.endsWith('/auth/permissions')) data={permissions:user.permissions,roles:user.roles,permission_names:{}}
    else if(path.endsWith('/notifications/unread-count')) data={count:0}
    else if(path.endsWith('/notifications')) data={items:[{id:41,title:'补投确认测试通知',read:false,wecom_status:'failed',can_redispatch:true,business_type:'customer',business_id:9,created_at:new Date().toISOString()}],total:1}
    else if(path.endsWith('/notifications/41/redispatch') && req.method==='POST') {redispatched++;data={sent:true}}
    else if(path.endsWith('/notifications/41/read') && req.method==='POST') {markedRead++;data={}}
    else if(path.endsWith('/dashboard/summary')) data={todo_count:1,stale_customer_days:30,stale_customer_count:0}
    else if(path.endsWith('/dashboard/team')) data={is_team_view:false}
    else if(path.endsWith('/dashboard/tasks')) data=[task]
    else if(path.endsWith('/tasks/7/complete') && req.method==='POST') { completed++; completedBodies.push(body); data={...task,status:'done'} }
    else if(path.endsWith('/tasks/handover-candidates')) data={items:[{id:8,name:'隔离接手同事',username:'successor'}],total:1}
    else if(path.endsWith('/tasks/batch-handover') && req.method==='POST') {handoverBodies.push(body);data={task_ids:body.task_ids}}
    else if(path.endsWith('/tasks') && url.searchParams.get('handover')==='true') {
      const rows=handoverBodies.length ? [] : [{
        ...task,id:407,title:'隔离待交接任务',handover_from_user_id:25,handover_from_user_name:'隔离已停用员工',
        receivable_plans:[
          {id:1,plan_name:'第一期',due_date:'2026-11-19',amount_exact:'100.00',remaining_amount_exact:'80.00'},
          {id:2,plan_name:'第二期',due_date:'2026-11-20',amount_exact:'980.00',remaining_amount_exact:'980.00'},
        ],
      }]
      data={items:rows,total:rows.length,page:1,page_size:20}
    }
    else if(path.endsWith('/tasks')) data={items:[task],total:1,page:1,page_size:20}
    else if(path.endsWith('/tasks/batch-complete') && req.method==='POST') { batchBodies.push(body); data={completed:[7],skipped:[]} }
    else if(path.endsWith('/custom-inquiries')) data={items:inquiryVersions,total:inquiryVersions.length}
    else if(path.endsWith('/custom-inquiries/401/history')) data=inquiryVersions.slice(0,2)
    else if(path.endsWith('/inquiries/oa-submission-options')) data={configured:false,enabled:false,fields:[],quote_owners:[]}
    else if(path.endsWith('/cases/405') && req.method==='DELETE') {
      caseFixture.status='withdrawn';caseFixture.status_label='已撤下';data=null
    }
    else if(path.endsWith('/cases')) {
      const rows=caseFixture.status==='published' || url.searchParams.get('include_history')==='true' ? [caseFixture] : []
      data={items:[...rows,{id:406,title:'隔离本人案例',status:'pending_review',status_label:'待审核',author_id:1}],total:rows.length+1}
    }
    else if(path.endsWith('/opportunities/408')) data={id:408,name:'隔离推荐商机',status:'open',stage:'quote'}
    else if(path.endsWith('/agent/product-recommendation')) data={recommendations:[
      {name:'隔离多价格商品',last_unit_price:null,last_unit_price_min_exact:'8.0000',last_unit_price_max_exact:'10.0000'},
      {name:'隔离四位单价商品',last_unit_price:8.2345,last_unit_price_min_exact:'8.2345',last_unit_price_max_exact:'8.2345'},
    ]}
    else if(path.endsWith('/opportunities')) data={items:[],total:0}
    else if(path==='/api/v1/quotes') data={items:exactQuote?[{id:121,quote_no:'EXACT-QUOTE-121',current_version_no:2,current_version_amount:99999999999999.98,current_version_amount_exact:'99999999999999.99',currency:'CNY',status:'accepted',status_label:'已接受'}]:[],total:exactQuote?1:0}
    else if(path.endsWith('/quotes/121/version-comparison')) data={versions:exactQuote?[{version_id:131,version_no:2,approval_status:'approved',currency:'CNY',quantity:100,item_count:1,sku_count:1,amount_total:99999999999999.98,amount_total_exact:'99999999999999.99',cost_total:null,profit_total:null,margin:null,avg_price:null,charges:[]}]:[],diffs:exactQuote?[{from_version_no:1,to_version_no:2,amount_delta:0.01,amount_delta_exact:'0.01',profit_delta:0.01,profit_delta_exact:'0.01',margin_delta:0,changes:[{sku_id:null,field:'charge_amount',before:99999999999999.98,after:99999999999999.98,before_exact:'99999999999999.98',after_exact:'99999999999999.99'},{sku_id:1,field:'quoted_price',before:999999999999.9991,after:999999999999.9991,before_exact:'999999999999.9991',after_exact:'999999999999.9992'},{sku_id:1,field:'quantity',before:9999999999999.988,after:9999999999999.988,before_exact:'9999999999999.988',after_exact:'9999999999999.989'}]}]:[],latest_version_id:131}
    else if(path.endsWith('/quotes/121') && req.method==='DELETE') {
      quoteDeletions.push(121)
      if(rejectQuoteDeletion) {
        res.writeHead(400,{'Content-Type':'application/json'})
        res.end(JSON.stringify({code:42201,message:'该报价已转成订单（id=999），不能删除',data:null}));return
      }
      await new Promise(resolve=>setTimeout(resolve,700))
      data=null
    }
    else if(path.endsWith('/quotes/121/versions')) data=[quoteVersion(131),quoteVersion(130)]
    else if(/\/quote-versions\/(130|131)$/.test(path)) data={version:quoteVersion(Number(path.split('/').at(-1))),quote:{id:121,quote_no:'ISO-QUOTE-121',customer_id:9,customer_name:'隔离报价客户',status:exactQuote?'accepted':'pending',status_label:exactQuote?'已接受':'审批中',current_version_id:131},items:quoteItems,charges:[],approval:null,cost_problems:quoteCostProblems}
    else if(path.endsWith('/quote-versions/131/cost-refresh') && req.method==='POST') {costRefreshes.push(131);quoteCostProblems=[];data={refreshed:1}}
    else if(path.endsWith('/products/201')) data={id:201,name:'隔离成本产品',status:'active',sku_count:1}
    else if(path.endsWith('/products/201/skus') && req.method==='POST') {skuCreates.push(body);data={id:202,product_id:201,sku_code:body.sku_code||'SKU-AUTO-202',...body}}
    else if(path.endsWith('/products/201/skus')) data=[{id:1,product_id:201,sku_code:'COST-SKU',name:'成本SKU',status:'active',cost_status:{state:'missing',label:'成本未维护'}},...skuCreates.map((body,i)=>({id:202+i,product_id:201,sku_code:'SKU-AUTO-202',status:'active',...body}))]
    else if(path.endsWith('/sku-master/skus/1')) {
      const pending_diffs=++skuDiffReads>1 ? [] : [{
        id:409,diff_type:'field_conflict',field_label:'名称',current_value:'CRM 名称',incoming_value:'外部名称',
        requires_note:true,allowed_resolutions:['keep_local'],
      }]
      data={fields:[],notes:[],pending_diffs,pending_diff_count:pending_diffs.length,latest_confirmed_version:0}
    }
    else if(path.endsWith('/sku-master/diffs/409/confirm')) {
      skuDiffWrites.push(body)
      res.writeHead(409,{'Content-Type':'application/json'})
      res.end(JSON.stringify({code:40901,message:'该差异已过期，请刷新后重新核定'}));return
    }
    else if(path.endsWith('/files/batch')) data={}
    else if(path.endsWith('/skus/1/costs')) {costPageRequests.push(path);data=[]}
    else if(path.endsWith('/pricing/sku-options')) data=[{id:1,sku_code:'COST-SKU',name:'成本SKU'}]
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
    else if(/\/samples\/\d+\/dingtalk$/.test(path) && req.method==='GET') data=sampleOaFixture(Number(path.split('/').at(-2)))
    else if(/\/samples\/\d+\/dingtalk-submit$/.test(path) && req.method==='POST') {
      const id=Number(path.split('/').at(-2))
      sampleRequests.push({id,action:'oa-submit',body})
      data={id:800+id,inquiry_id:0,inquiry_version:0,oa_type:'sample',provider:'self_oa',instance_id:`ISO-OA-${id}`,status:'pending',status_label:'审批中',submit_round:1,attempt_count:1,allowed_actions:[]}
    }
    else if(/\/samples\/\d+\/(approve|resubmit|ship|sign|revise)$/.test(path) && req.method==='POST') {
      const id=Number(path.split('/').at(-2)),action=path.split('/').at(-1)
      sampleRequests.push({id,action,body});data=sampleFixture(action==='revise'?id+100:id)
    }
    else if(path.endsWith('/integrations/wecom/readiness')) data={configured:{corp_id:true,agent_id:true,contact_secret:true,external_contact_secret:true,callback:true},counts:{wecom_users:0,external_contacts:0,follow_relations:0,unbound_contacts:0,unmatched_users:0},can_sync_contact:true,can_sync_external:true}
    else if(path.endsWith('/integrations/wecom/unbound-contacts')) data={items:[wecomContact],total:1,page:1,page_size:20}
    else if(path.endsWith('/integrations/wecom/sync-jobs')) data={items:failedWecomJobs,total:failedWecomJobs.length,page:1,page_size:20}
    else if(path.endsWith('/integrations/wecom/unbound-contacts/92/candidates')) data={contact:wecomContact,candidates:[{id:93,name:'隔离匹配客户甲',score:80,reasons:[]},{id:94,name:'隔离匹配客户乙',score:85,reasons:[]}]}
    else if(path.endsWith('/integrations/wecom/unbound-contacts/92/bind-customer') && req.method==='POST') {wecomBindings.push({contact_id:92,...body});data={contact_id:92,customer_id:body.customer_id,customer_name:'隔离匹配客户乙'}}
    else if(/\/integrations\/wecom\/sync-(departments|users|external-contacts|follow-relations)$/.test(path) && req.method==='POST') {
      wecomRequests.push(path.split('/').at(-1))
      if(rejectWecomSync) {
        failedWecomJobs.push({id:909,job_type:'department',job_type_label:'隔离失败任务',status:'failed',status_label:'失败',success_count:0,fail_count:1,started_at:new Date().toISOString(),error_message:'连接企业微信失败，请检查网络后重试'})
        res.writeHead(502,{'Content-Type':'application/json'});res.end(JSON.stringify({code:50202,message:'连接企业微信失败，请检查网络后重试',data:null}));return
      }
      data={id:91,status:'success',success_count:0,fail_count:0}
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
  let browserStderr=''
  // --disable-gpu/--no-sandbox：Chrome 158 起（2026-11-24 本机实证）macOS 上子进程沙箱初始化
  // 失败（aperitif: Failed to initialize sandbox）→ GPU 进程反复崩 → FATAL 自杀，
  // CDP 全程无响应。冒烟只读 DOM+点击、页面全是自家 dist+本机 mock，不需要 GPU 也不受沙箱保护。
  browser=spawn(executable,['--headless=new','--no-first-run','--no-default-browser-check','--disable-background-networking','--disable-extensions','--disable-gpu','--no-sandbox','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'],{stdio:['ignore','ignore','pipe']})
  browser.stderr.on('data',chunk=>{browserStderr=(browserStderr+chunk.toString()).slice(-8000)})
  // CI 机器忙时 Chrome 启动能超 10 秒（2026-11-16 晚两条 browser timeout 假红），上限放宽到 60 秒
  for(let i=0;!existsSync(join(profile,'DevToolsActivePort'));i++){if(i>600)throw Error(`browser timeout (exit=${browser.exitCode}, signal=${browser.signalCode}): ${browserStderr}`);await sleep(100)}
  const debug=readFileSync(join(profile,'DevToolsActivePort'),'utf8').split('\n')[0]
  const target=await (await fetch(`http://127.0.0.1:${debug}/json/new?about:blank`,{method:'PUT'})).json()
  socket=new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject})
  let next=1;const pending=new Map(), browserErrors=[]
  socket.onmessage=event=>{const data=JSON.parse(event.data);if(data.id){const p=pending.get(data.id);if(p){clearTimeout(p.timer);pending.delete(data.id);data.error?p.reject(Error(JSON.stringify(data.error))):p.resolve(data.result)}}
    if(data.method==='Runtime.consoleAPICalled' && data.params.type==='error') browserErrors.push(...data.params.args.map(arg=>arg.description??arg.value).filter(Boolean))
  }
  const send=(method,params={})=>new Promise((resolve,reject)=>{
    const id=next++
    const timer=setTimeout(()=>{pending.delete(id);reject(Error(`browser command timed out: ${method}`))},15000)
    pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({id,method,params}))
  })
  const evaluate=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error((r.exceptionDetails.exception?.description ?? r.exceptionDetails.text)+'\nExpression: '+expression);return r.result.value}
  const waitFor=async expression=>{for(let i=0;i<200;i++){if(await evaluate(`Boolean(document.body && (${expression}))`))return;await sleep(100)}throw Error('condition timed out: '+expression+'\n'+await evaluate('document.body?.innerText.slice(0,1800) ?? "<body not ready>"')+'\n'+browserErrors.slice(-4).join('\n'))}
  await send('Page.enable')
  await send('Runtime.enable')
  const auth=JSON.stringify({state:{token:'isolated-local-only',user},version:0})
  await send('Page.addScriptToEvaluateOnNewDocument',{source:`localStorage.setItem('crm-auth',${JSON.stringify(auth)})`})
  // 页面导航存在 body 尚未建立的窗口；等待器必须继续等，不能因读取 innerText 抛错。
  await evaluate(`document.body.remove();setTimeout(()=>{const body=document.createElement('body');body.textContent='navigation-body-ready';document.documentElement.appendChild(body)},200)`)
  await waitFor(`document.body.innerText.includes('navigation-body-ready')`)
  console.log('BROWSER_NAVIGATION_BODY_WAIT=true')
  {
    const navigate = (route) => send('Page.navigate', {url:`http://127.0.0.1:${port}/${route}`})
    const matchText = (selector, label) =>
      `Array.from(document.querySelectorAll(${JSON.stringify(selector)})).find(el=>el.innerText.trim()===${JSON.stringify(label)})`
    const clickText = async (selector, label) => {
      const match = matchText(selector, label)
      await waitFor(`${match} && !${match}.disabled`)
      await evaluate(`${match}.click()`)
    }
    await navigate('tasks')
    await clickText('button', '待办交接')
    await waitFor(`document.body.innerText.includes('原负责人 · 隔离已停用员工')`)
    await clickText('summary', '查看 2 期应收')
    await waitFor(`document.body.innerText.includes('第二期 · 2026-11-20') && document.body.innerText.includes('未收 ¥980.00')`)
    await evaluate(`document.querySelector('.tasks-table input[type="checkbox"]').click()`)
    await clickText('button', '批量交接（已选 1）')
    await evaluate(`document.querySelector('.semi-modal-content .semi-select').click()`)
    await clickText('.semi-select-option', '隔离接手同事（successor）')
    assert.deepEqual(handoverBodies, [])
    await clickText('.semi-modal-content button', '确认交接')
    await waitFor(`document.body.innerText.includes('没有待交接的任务')`)
    assert.deepEqual(handoverBodies,[{task_ids:[407],owner_id:8}])
    console.log('BROWSER_TASK_HANDOVER_AND_RECEIVABLE_PLANS=true')

    await navigate('inquiries')
    await clickText('a, button.crm-text-action', '查看新版')
    await waitFor(`document.querySelector('.semi-modal-content')?.innerText.includes('按此版本生成报价草稿')`)
    await clickText('.semi-modal-content :is(a, button.crm-text-action)', '按此版本生成报价草稿')
    await waitFor(`document.querySelector('.semi-modal-content')?.innerText.includes('生成报价草稿：隔离询价新版')`)
    await clickText('.semi-modal-content button', '取消')
    await clickText('a, button.crm-text-action', '修订后报价')
    await waitFor(`document.querySelector('.semi-modal-content')?.innerText.includes('修订：隔离归档询价')`)
    await clickText('.semi-modal-content button', '取消')
    await clickText('a, button.crm-text-action', '查看原报价')
    await waitFor(`location.pathname === '/quotes/121'`)
    assert.equal(apiRequests.some(r=>r.path.endsWith('/create-quote')),false)
    console.log('BROWSER_INQUIRY_LATEST_REVISION_AND_ORIGINAL_QUOTE=true')

    await navigate('products/201')
    await clickText('a, button.crm-text-action', '来源 / 差异')
    await waitFor(`document.querySelector('.semi-modal-content')?.innerText.includes('CRM 名称')`)
    await evaluate(`Array.from(document.querySelectorAll('.semi-modal-content .semi-select')).find(el=>el.innerText.includes('选择处理方式')).click()`)
    await clickText('.semi-select-option', '以本地为准')
    await waitFor(`Array.from(document.querySelectorAll('.semi-modal-content .semi-select')).some(el=>el.innerText.includes('以本地为准'))`)
    const noteInput = `document.querySelector('.semi-modal-content input[aria-label="核定依据"]')`
    await evaluate(`${noteInput}.focus()`)
    await send('Input.insertText',{text:'隔离核定依据'})
    await waitFor(`${noteInput}.value === '隔离核定依据'`)
    await clickText('.semi-modal-content button', '确认结论')
    await sleep(250)
    assert.deepEqual(skuDiffWrites,[{resolution:'keep_local',note:'隔离核定依据'}],await evaluate('document.body.innerText.slice(-900)'))
    await waitFor(`document.querySelector('.semi-modal-content')?.innerText.includes('该差异已过期，请刷新后重新核定')`)
    assert.equal(await evaluate(`${noteInput}.value`),'隔离核定依据')
    await clickText('.semi-modal-content button', '刷新差异')
    await waitFor(`document.querySelector('.semi-modal-content')?.innerText.includes('没有待确认差异')`)
    console.log('BROWSER_SKU_DIFF_BLOCKED_WITH_REASON_AND_REFRESH=true')

    // 先以普通业务员（案例作者、非审核人）查看：自己的待审案例只能看到“等待审核”，没有驳回。
    user.roles=['salesperson'];user.permissions=['quote:view']
    const caseAuthorAuth=JSON.stringify({state:{token:'isolated-local-only',user},version:0})
    await send('Page.addScriptToEvaluateOnNewDocument',{source:`localStorage.setItem('crm-auth',${JSON.stringify(caseAuthorAuth)})`})
    await navigate('cases')
    await waitFor(`document.body.innerText.includes('隔离本人案例')`)
    const authorRow=await evaluate(`Array.from(document.querySelectorAll('tr')).find(row=>row.innerText.includes('隔离本人案例')).innerText`)
    assert.ok(!authorRow.includes('驳回'),'author must not see reject action on own pending case')
    assert.ok(authorRow.includes('等待有权限的同事审核'),'author sees the waiting hint')
    // 再切回管理员（审核人）执行撤下，验证发布版可撤下并保留历史。
    user.roles=['admin'];user.permissions=[]
    const caseReviewerAuth=JSON.stringify({state:{token:'isolated-local-only',user},version:0})
    await send('Page.addScriptToEvaluateOnNewDocument',{source:`localStorage.setItem('crm-auth',${JSON.stringify(caseReviewerAuth)})`})
    await navigate('cases')
    await waitFor(`document.body.innerText.includes('隔离培训案例')`)
    await clickText('button', '撤下')
    await waitFor(`document.querySelector('.semi-popconfirm')?.innerText.includes('撤下这个案例版本')`)
    assert.equal(caseFixture.status,'published')
    await clickText('.semi-popconfirm button', '确定')
    await waitFor(`!document.querySelector('.semi-table-tbody')?.innerText.includes('隔离培训案例')`)
    await evaluate(`document.querySelector('.toolbar input[type="checkbox"]').click()`)
    await waitFor(`document.querySelector('.semi-table-tbody')?.innerText.includes('已撤下')`)
    console.log('BROWSER_CASE_NO_SELF_REVIEW_WITHDRAW_AND_HISTORY=true')

    await navigate('opportunities/408')
    await waitFor(`document.body.innerText.includes('产品推荐')`)
    await evaluate(`Array.from(document.querySelectorAll('.card-sub-head')).find(el=>el.innerText.includes('产品推荐')).querySelector('button').click()`)
    await waitFor(`document.body.innerText.includes('8–10 元') && document.body.innerText.includes('¥8.2345')`)
    assert.ok(await evaluate(`!document.body.innerText.includes('未命名字段')`))
    console.log('BROWSER_PRODUCT_RECOMMENDATION_EXACT_RANGE=true')
  }
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
  const retryLink=`Array.from(document.querySelectorAll('.notice-panel button')).find(b=>b.innerText.trim()==='补投')`
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
  const restoreLink=`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='恢复')`
  await waitFor(restoreLink)
  await evaluate(`${restoreLink}.focus()`)
  assert.equal(await evaluate(`document.activeElement === ${restoreLink}`),true)
  await send('Input.dispatchKeyEvent',{type:'keyDown',key:' ',code:'Space',windowsVirtualKeyCode:32,text:' '})
  await send('Input.dispatchKeyEvent',{type:'keyUp',key:' ',code:'Space',windowsVirtualKeyCode:32})
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
  const finishLink=`Array.from(document.querySelectorAll('.semi-table-tbody button')).find(a=>a.innerText.trim()==='完成')`
  await waitFor(finishLink)
  assert.equal(await evaluate(`Array.from(document.querySelectorAll('.semi-table-tbody a')).find(a=>a.innerText.trim()==='隔离任务客户')?.getAttribute('href')`),'/customers/2')
  assert.equal(await evaluate(`document.querySelector('.semi-table-tbody').innerText.includes('客户编号')`),false)
  console.log('BROWSER_TASK_CUSTOMER_NAME_AND_EXACT_DETAIL_LINK=true')
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
  const claimLead=`Array.from(document.querySelectorAll('.semi-table-tbody tr')).find(r=>r.innerText.includes('线索乙'))?.querySelector('button')`
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
  // 领取确认自 2b88f5c 起走 Modal.confirm（弹窗），不是 Popconfirm（气泡）——容器要对准 .semi-modal-content
  const claimModal=`document.querySelector('.semi-modal-content')`
  await waitFor(claimCustomer)
  await evaluate(`${claimCustomer}.click()`)
  await waitFor(`document.body.innerText.includes('确认领取客户「领取确认测试客户」')`)
  assert.equal(claimedCustomers.length,0)
  await evaluate(`Array.from(${claimModal}.querySelectorAll('button')).find(b=>b.innerText.includes('取消')).click()`)
  await sleep(250)
  assert.equal(claimedCustomers.length,0)
  await evaluate(`${claimCustomer}.click()`)
  await waitFor(`document.body.innerText.includes('确认领取客户「领取确认测试客户」')`)
  await evaluate(`Array.from(${claimModal}.querySelectorAll('button')).find(b=>b.innerText.trim()==='确定').click()`)
  for(let i=0;claimedCustomers.length===0 && i<30;i++)await sleep(100)
  assert.deepEqual(claimedCustomers,[9])
  console.log('BROWSER_CUSTOMER_CLAIM_CANCEL_NO_WRITE_CONFIRM_CORRECT_ID=true')
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/approvals`})
  const approveLink=`Array.from(document.querySelectorAll('.semi-table-tbody tr')).find(r=>r.innerText.includes('隔离审批-52')) && Array.from(Array.from(document.querySelectorAll('.semi-table-tbody tr')).find(r=>r.innerText.includes('隔离审批-52')).querySelectorAll('button')).find(a=>a.innerText.trim()==='通过')`
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
  const rejectLink=`Array.from(Array.from(document.querySelectorAll('.semi-table-tbody tr')).find(r=>r.innerText.includes('隔离审批-51')).querySelectorAll('button')).find(a=>a.innerText.trim()==='拒绝')`
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
  // clickText 在上面 tasks 段的块作用域里，这里够不着（原用例组一直用裸 evaluate 也是这个原因）。
  // OA 弹窗操作统一带「等按钮可用再点」，局部补一个等价 helper。
  const clickModalButton = async label => {
    const match = `Array.from(document.querySelectorAll('.semi-modal-content button')).find(b=>b.innerText.trim()===${JSON.stringify(label)})`
    await waitFor(`${match} && !${match}.disabled`)
    await evaluate(`${match}.click()`)
  }
  // 61/62/63/67：批准/拒绝/重提/改判四按钮已随 bdd6efc9 收进「同序 OA 打样流程」弹窗（产品有意改版，
  // 本地 /approve /resubmit 收口成 409「打样审批统一在同序 OA 办理」）。测试跟上新载体：
  // 开/关弹窗不写库，写库只发生在点提交，且钉正确单据 id。62 是已送审在审态（弹窗只读，无提交按钮）。
  for(const [id,openLabel,writePath] of [
    [61,'填写或提交 OA 申请','submit'],
    [62,'查看 OA 进度',null],
    [63,'查看 OA 审批意见','resubmit'],
    [67,'查看 OA 审批意见','resubmit'],
  ]) {
    await send('Page.navigate',{url:`http://127.0.0.1:${port}/samples/${id}`})
    const button=`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()===${JSON.stringify(openLabel)})`
    await waitFor(button)
    const before=sampleRequests.length
    await evaluate(`${button}.click()`)
    await waitFor(`document.body.innerText.includes('同序 OA 打样流程')`)
    assert.equal(sampleRequests.length,before,'opening the OA dialog must not write')
    await clickModalButton('关闭')
    await waitFor(`!document.body.innerText.includes('同序 OA 打样流程')`)
    assert.equal(sampleRequests.length,before,'closing the OA dialog must not write')
    if(!writePath) {
      console.log(`BROWSER_SAMPLE_${id}_OA_OPEN_CLOSE_NO_WRITE=true`)
      continue
    }
    await evaluate(`${button}.click()`)
    await waitFor(`document.body.innerText.includes('同序 OA 打样流程')`)
    if(writePath==='resubmit') {
      // 「修改后重新提交」只把表单切回可编辑态（本地 state），不发请求
      await clickModalButton('修改后重新提交')
      assert.equal(sampleRequests.length,before,'resubmit edit state must not write')
    }
    await clickModalButton('提交打样到同序 OA')
    for(let i=0;sampleRequests.length===before && i<30;i++)await sleep(100)
    assert.equal(sampleRequests.length,before+1)
    assert.equal(sampleRequests.at(-1).id,id)
    assert.equal(sampleRequests.at(-1).action,'oa-submit')
    console.log(`BROWSER_SAMPLE_${id}_OA_${writePath.toUpperCase()}_OPEN_CLOSE_NO_WRITE_CONFIRM_CORRECT_ID=true`)
  }
  for(const [id,label,title,action] of [
    [64,'登记寄样','确认登记寄样','ship'],
    [65,'登记签收','确认登记客户已签收','sign'],
    [66,'开新修订版（改车间依据）','确认开新修订版','revise'],
  ]) {
    await send('Page.navigate',{url:`http://127.0.0.1:${port}/samples/${id}`})
    const button=`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()===${JSON.stringify(label)})`
    await waitFor(button)
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
    console.log(`BROWSER_SAMPLE_${id}_${action}_CANCEL_NO_WRITE_CONFIRM_CORRECT_ID=true`)
  }
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/wecom`})
  await waitFor(`document.body.innerText.includes('先同步企微成员')`)
  assert.deepEqual(wecomRequests, [], 'the next-step hint must not start a sync')
  const syncMenuButton=`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim().startsWith('更新企微数据') && !b.disabled)`
  const chooseWecomSync=async label=>{
    const before=wecomRequests.length
    const option=`Array.from(document.querySelectorAll('.semi-dropdown-item > div > div:first-child')).find(el=>el.innerText.trim()===${JSON.stringify(label)})`
    // 失败任务刷新会让页面重渲染，下拉菜单可能刚展开就被关掉。
    // 每次重新打开再找，点不到就再来一轮，最多三轮。
    for(let attempt=0;attempt<3;attempt++) {
      await waitFor(syncMenuButton)
      await evaluate(`${syncMenuButton}.click()`)
      try { await waitFor(option) } catch { continue }
      assert.equal(wecomRequests.length,before,'opening the sync menu must not submit')
      const clicked=await evaluate(`(()=>{const el=${option};if(!el)return false;el.click();return true})()`)
      if(!clicked) continue
      for(let i=0;wecomRequests.length===before && i<30;i++)await sleep(100)
      if(wecomRequests.length===before+1) return
    }
    throw Error('企微同步菜单没有提交：'+label)
  }
  // 部门已改为以同序 OA 为准，企微页面不再提供「同步部门」入口。
  for(const [label,endpoint] of [
    ['同步成员','sync-users'],
    ['同步外部联系人','sync-external-contacts'],['同步跟进关系','sync-follow-relations'],
  ]) {
    await chooseWecomSync(label)
    assert.equal(wecomRequests.at(-1),endpoint,'sync must call its own endpoint')
    console.log(`BROWSER_WECOM_${endpoint}_MENU_NO_WRITE_SELECT_CORRECT_ENDPOINT=true`)
  }
  rejectWecomSync=true
  await chooseWecomSync('同步成员')
  await waitFor(`document.body.innerText.includes('隔离失败任务')`)
  assert.equal(failedWecomJobs.length,1)
  rejectWecomSync=false
  await chooseWecomSync('同步成员')
  assert.equal(wecomRequests.at(-1),'sync-users')
  console.log('BROWSER_WECOM_FAILURE_REFRESH_AND_RETRY=true')
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
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/quotes/121?version=9999`})
  const withdraw=`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='撤回审批')`
  await waitFor(withdraw)
  assert.ok(apiRequests.some(({path})=>path.endsWith('/quote-versions/131')))
  assert.ok(!apiRequests.some(({path})=>path.endsWith('/quote-versions/9999')))
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
  const deleteQuoteButton=`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='删除报价')`
  await waitFor(deleteQuoteButton)
  await evaluate(`${deleteQuoteButton}.click()`)
  await waitFor(`document.body.innerText.includes('确认删除报价「ISO-QUOTE-121」')`)
  assert.equal(quoteDeletions.length,0)
  const quoteDeleteDialog=`document.querySelector('.semi-modal-content')`
  await evaluate(`Array.from(${quoteDeleteDialog}.querySelectorAll('button')).find(b=>b.innerText.trim()==='取消').click()`)
  await sleep(250)
  assert.equal(quoteDeletions.length,0)
  assert.equal(await evaluate('location.pathname'),'/quotes/121')
  rejectQuoteDeletion=true
  await evaluate(`${deleteQuoteButton}.click()`)
  await waitFor(`document.body.innerText.includes('确认删除报价「ISO-QUOTE-121」')`)
  await evaluate(`Array.from(${quoteDeleteDialog}.querySelectorAll('button')).find(b=>b.innerText.trim()==='确认删除').click()`)
  await waitFor(`document.body.innerText.includes('该报价已转成订单（id=999），不能删除')`)
  assert.deepEqual(quoteDeletions,[121])
  assert.equal(await evaluate('location.pathname'),'/quotes/121')
  assert.equal(await evaluate(`Boolean(${quoteDeleteDialog})`),true)
  rejectQuoteDeletion=false
  await evaluate(`{ const button=Array.from(${quoteDeleteDialog}.querySelectorAll('button')).find(b=>b.innerText.trim()==='确认删除'); button.click(); button.click(); }`)
  await waitFor(`Array.from(${quoteDeleteDialog}.querySelectorAll('button')).find(b=>b.innerText.trim()==='取消')?.disabled`)
  await waitFor(`location.pathname==='/quotes'`)
  assert.deepEqual(quoteDeletions,[121,121],'each confirmation sends one whole-quote DELETE, even while viewing a historical version')
  console.log('BROWSER_QUOTE_DELETE_CANCEL_NO_WRITE_FAILURE_STAYS_SUCCESS_RETURNS_LIST=true')
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
    const restore=`Array.from(document.querySelectorAll('.semi-table-tbody tr')).find(r=>r.innerText.includes(${JSON.stringify(name)}))?.querySelector('button.crm-text-action:last-child')`
    await waitFor(restore)
    assert.equal(await evaluate(`${restore}.innerText.trim()`),'恢复')
    if(tab==='产品') assert.equal(await evaluate(`Array.from(document.querySelectorAll('.semi-table-tbody tr')).find(r=>r.innerText.includes('ISO-SKU-107')).querySelectorAll('button.crm-text-action').length`),0,'SKU whose product is deleted must have no restore action')
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
  const reassignRestore=`Array.from(document.querySelectorAll('.semi-table-tbody tr')).find(r=>r.innerText.includes('待重新分配客户'))?.querySelector('button.crm-text-action:last-child')`
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
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/pool-recycle`})
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
  // 回收决策已搬到「客户中心 › 回收复核」，扫描按钮仍留在系统设置的规则页。
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/settings?tab=rules&section=pool`})
  await waitFor(`Array.from(document.querySelectorAll('button')).some(b=>b.innerText.trim()==='扫描并生成回收预告')`)
  for(const [label,title,path] of [
    ['扫描并生成回收预告','确认扫描并生成回收预告','/api/v1/public-pool/run-recycle'],
    ['立即扫描','确认立即扫描自动待办','/api/v1/tasks/run-auto-rules'],
  ]) {
    if (path.endsWith('run-auto-rules')) {
      const tab=`Array.from(document.querySelectorAll('.rule-tabs button')).find(b=>b.innerText.trim()==='自动任务')`
      await waitFor(tab)
      await evaluate(`${tab}.click()`)
    }
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
  costDraft=true
  quoteCostProblems=[{item_id:401,sku_id:1,label:'COST-SKU',reason:'成本未维护'}]
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/quotes/121?version=131`})
  await waitFor(`document.getElementById('quote-cost-status')?.innerText.includes('成本未完整确认')`)
  assert.ok(await evaluate(`document.body.innerText.includes('成本未完整确认，暂不能计算利润')`))
  assert.equal(await evaluate(`document.querySelector('#quote-cost-status a').getAttribute('href')`),'/prices?tab=costs&sku_id=1')
  const refreshCostButton=`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='更新草稿成本')`
  await evaluate(`${refreshCostButton}.click()`)
  await waitFor(`document.body.innerText.includes('用当前成本更新草稿？')`)
  await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.trim()==='取消').click()`)
  assert.equal(costRefreshes.length,0)
  await evaluate(`${refreshCostButton}.click()`)
  await waitFor(`document.body.innerText.includes('用当前成本更新草稿？')`)
  await evaluate(`Array.from(${popup}.querySelectorAll('button')).find(b=>b.innerText.trim()==='确定').click()`)
  await waitFor(`!document.getElementById('quote-cost-status')`)
  assert.deepEqual(costRefreshes,[131])
  console.log('BROWSER_QUOTE_COST_HINT_CANCEL_NO_WRITE_EXPLICIT_REFRESH=true')
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/products/201`})
  await waitFor(`document.body.innerText.includes('成本未维护')`)
  await evaluate(`Array.from(document.querySelectorAll('a, button.crm-text-action')).find(a=>a.innerText.trim()==='去维护').click()`)
  await waitFor(`location.pathname==='/prices' && document.body.innerText.includes('新增成本')`)
  assert.equal(await evaluate('location.search'),'?tab=costs&sku_id=1')
  assert.ok(costPageRequests.length>0,'cost shortcut loads the exact SKU costs')
  console.log('BROWSER_PRODUCT_COST_STATUS_AND_EXACT_SKU_MAINTENANCE_SHORTCUT=true')
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/products/201`})
  const newSkuButton=`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='新建 SKU')`
  await waitFor(newSkuButton)
  await evaluate(`${newSkuButton}.click()`)
  await waitFor(`document.querySelector('input[placeholder="有公司编码可填写；留空自动生成"]')`)
  await evaluate(`(()=>{for(const [placeholder,value] of [['例如：冷链保温箱 60L 白色','周转箱蓝色'],['600×400×300mm','600×400']]){const el=document.querySelector('input[placeholder="'+placeholder+'"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,value);el.dispatchEvent(new Event('input',{bubbles:true}))}})()`)
  await evaluate(`Array.from(document.querySelector('.semi-modal-content').querySelectorAll('button')).find(b=>b.innerText.trim()==='创建').click()`)
  await waitFor(`!document.querySelector('.semi-modal-content')`)
  assert.equal(skuCreates.length,1)
  assert.equal(skuCreates[0].sku_code,undefined)
  assert.equal(skuCreates[0].name,'周转箱蓝色')
  assert.equal(skuCreates[0].specification,'600×400')
  await waitFor(`document.body.innerText.includes('周转箱蓝色') && document.body.innerText.includes('SKU-AUTO-202')`)
  const skuCell=await evaluate(`Array.from(document.querySelectorAll('.semi-table-tbody tr')).find(r=>r.innerText.includes('周转箱蓝色')).querySelector('td').innerText`)
  assert.ok(skuCell.indexOf('周转箱蓝色')<skuCell.indexOf('SKU-AUTO-202'),'human name precedes auxiliary code')
  console.log('BROWSER_SKU_EMPTY_CODE_CREATE_PRESERVES_NAME_SPEC_AND_NAME_FIRST=true')
  user.roles=['sales'];user.permissions=['quote:view','product:view']
  const restrictedAuth=JSON.stringify({state:{token:'isolated-local-only',user},version:0})
  await send('Page.addScriptToEvaluateOnNewDocument',{source:`localStorage.setItem('crm-auth',${JSON.stringify(restrictedAuth)})`})
  quoteCostProblems=[{item_id:401,sku_id:1,label:'COST-SKU',reason:'成本未维护'}]
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/quotes/121?version=131`})
  await waitFor(`document.getElementById('quote-cost-status')?.innerText.includes('请联系价格管理员')`)
  assert.equal(await evaluate(`document.querySelectorAll('#quote-cost-status a').length`),0)
  console.log('BROWSER_QUOTE_COST_RESTRICTED_USER_SEES_STATUS_AND_ADMIN_GUIDANCE=true')
  quoteCostProblems=[]
  quoteItems=[{id:401,sku_id:1,sku_code:'REDACTED-COST-SKU',quantity:10,quoted_price:29,amount:290,cost_snapshot:null,logistics_cost_snapshot:null,profit_snapshot:null,profit_rate_snapshot:null,minimum_price_snapshot:null}]
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/quotes/121?version=131`})
  await waitFor(`document.querySelector('.semi-table-tbody')?.innerText.includes('REDACTED-COST-SKU')`)
  assert.equal(await evaluate(`document.body.innerText.includes('这个页面没能加载出来')`),false)
  const redactedRow=await evaluate(`Array.from(document.querySelectorAll('.semi-table-tbody tr')).find(r=>r.innerText.includes('REDACTED-COST-SKU')).innerText`)
  assert.ok(!redactedRow.includes('0.0%'),'redacted profit must not become zero margin')
  console.log('BROWSER_QUOTE_REDACTED_COST_NULL_RENDERS_WITHOUT_CRASH=true')
  quoteItems=[]
  // 普通业务员的个人页不依赖管理权限，管理深链也只能回到个人页。
  user.roles=['salesperson'];user.permissions=['customer:view']
  const salesAuth=JSON.stringify({state:{token:'isolated-local-only',user},version:0})
  await send('Page.addScriptToEvaluateOnNewDocument',{source:`localStorage.setItem('crm-auth',${JSON.stringify(salesAuth)})`})
  const personalStart=apiRequests.length
  for(const tab of ['profile','roles','rules']) {
    await send('Page.navigate',{url:`http://127.0.0.1:${port}/settings?tab=${tab}`})
    await waitFor(`document.querySelector('input[aria-label="个人登录名"]')`)
    assert.ok(await evaluate(`document.body.innerText.includes('我的信息') && document.body.innerText.includes('我的权限')`))
    assert.equal(await evaluate(`document.querySelectorAll('.permission-denied').length`),0)
    assert.equal(await evaluate(`Array.from(document.querySelectorAll('[role="tab"]')).some(t=>['用户','角色与数据范围','业务规则'].includes(t.innerText.trim()))`),false)
  }
  assert.equal(apiRequests.slice(personalStart).some(r=>/\/(users|roles|departments|settings|public-pool)($|\/)/.test(r.path)),false,'personal view must not request management APIs')
  console.log('BROWSER_SALESPERSON_PERSONAL_ACCESS_AND_MANAGEMENT_DEEPLINK_BOUNDARY=true')
  const exportStart=apiRequests.length
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/customers`})
  await waitFor(`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='导出')`)
  for(const width of [1440,1280,1024]) {
    await send('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false})
    const layout=await evaluate(`(()=>{
      const toolbar=document.querySelector('.page-container .toolbar');
      const button=text=>Array.from(toolbar.querySelectorAll('button')).find(b=>b.innerText.trim()===text);
      const rect=el=>{const r=el.getBoundingClientRect();return {top:r.top,bottom:r.bottom}};
      return {export:rect(button('导出')),import:rect(button('批量导入')),hint:rect(toolbar.querySelector('[data-blocked-hint]')),count:toolbar.innerText.split('导出需要单独授权').length-1,disabled:button('导出').disabled};
    })()`)
    assert.equal(layout.count,1,'export permission reason must appear once')
    assert.equal(layout.disabled,true,'missing export permission stays disabled')
    assert.ok(layout.hint.top>=layout.export.bottom && layout.hint.top>=layout.import.bottom,'hint follows all action buttons')
  }
  await evaluate(`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='导出').parentElement.click()`)
  await waitFor(`document.querySelector('.semi-toast-content')?.innerText.includes('客户导出')`)
  assert.equal(await evaluate(`document.querySelectorAll('.semi-modal-content').length`),0)
  assert.equal(apiRequests.slice(exportStart).some(r=>r.path.endsWith('/customers/export')),false)
  console.log('BROWSER_CUSTOMER_EXPORT_SINGLE_HINT_LAYOUT_AND_NO_UNAUTHORIZED_EXPORT=true')
  user.permissions=['customer:view','customer:export']
  const exportAuth=JSON.stringify({state:{token:'isolated-local-only',user},version:0})
  await send('Page.addScriptToEvaluateOnNewDocument',{source:`localStorage.setItem('crm-auth',${JSON.stringify(exportAuth)})`})
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/customers`})
  await waitFor(`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='导出' && !b.disabled)`)
  assert.equal(await evaluate(`document.querySelector('.page-container .toolbar').innerText.includes('导出需要单独授权')`),false)
  await evaluate(`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='导出').click()`)
  await waitFor(`document.querySelector('.semi-modal-content')?.innerText.includes('导出用途')`)
  assert.equal(apiRequests.slice(exportStart).some(r=>r.path.endsWith('/customers/export')),false,'opening export dialog must not download')
  console.log('BROWSER_AUTHORIZED_CUSTOMER_EXPORT_OPENS_PURPOSE_DIALOG=true')
  user.permissions=['task:view','customer:view']
  const taskAuth=JSON.stringify({state:{token:'isolated-local-only',user},version:0})
  await send('Page.addScriptToEvaluateOnNewDocument',{source:`localStorage.setItem('crm-auth',${JSON.stringify(taskAuth)})`})
  task.customer_name=null
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/tasks`})
  await waitFor(`document.querySelector('.semi-table-tbody')?.innerText.includes('客户不可查看')`)
  assert.equal(await evaluate(`document.querySelectorAll('.semi-table-tbody a[href="/customers/2"]').length`),0)
  assert.equal(await evaluate(`document.querySelector('.semi-table-tbody').innerText.includes('客户编号')`),false)
  task.customer_name='隔离任务客户';task.quote_id=121;task.quote_no='Q-TASK-121'
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/tasks`})
  await waitFor(`Array.from(document.querySelectorAll('.semi-table-tbody a')).some(a=>a.innerText.trim()==='报价 Q-TASK-121' && a.getAttribute('href')==='/quotes/121')`)
  console.log('BROWSER_TASK_UNAVAILABLE_CUSTOMER_NO_LINK_AND_QUOTE_PRIORITY_PRESERVED=true')

  financialRows=true;user.roles=['admin'];user.permissions=[]
  const financeAuth=JSON.stringify({state:{token:'isolated-local-only',user},version:0})
  await send('Page.addScriptToEvaluateOnNewDocument',{source:`localStorage.setItem('crm-auth',${JSON.stringify(financeAuth)})`})
  for(const route of ['orders','receivables']) {
    await send('Page.navigate',{url:`http://127.0.0.1:${port}/${route}`})
    await waitFor(`document.querySelector('.semi-table-tbody')?.innerText.includes('EXACT-MONEY-991')`)
    assert.ok(await evaluate(`document.querySelector('.semi-table-tbody').innerText.includes('99,999,999,999,999.99')`),route+' uses exact amount')
  }
  await evaluate(`Array.from(document.querySelectorAll('[role="tab"]')).find(el=>el.innerText.includes('回款记录')).click()`)
  await waitFor(`document.querySelector('.semi-table-tbody')?.innerText.includes('99,999,999,999,999.99')`)
  console.log('BROWSER_ORDER_RECEIVABLE_PAYMENT_PRESERVE_EXACT_CENTS=true')
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/orders/991`})
  await waitFor(`Array.from(document.querySelectorAll('[role="tab"]')).some(el=>el.innerText.includes('应收计划'))`)
  await waitFor(`Array.from(document.querySelectorAll('.kpi-label')).some(el=>el.innerText.trim().startsWith('已到账待分配'))`)
  for(const label of ['已分配到应收期','已到账待分配','待确认回款']) {
    await waitFor(`Array.from(document.querySelectorAll('.kpi-card')).find(el=>el.querySelector('.kpi-label')?.innerText.trim().startsWith(${JSON.stringify(label)}))?.querySelector('.kpi-value')?.innerText==='¥99,999,999,999,999.99'`)
  }
  console.log('BROWSER_ORDER_FINANCE_SUMMARY_EXACT_CENTS=true')
  await evaluate(`Array.from(document.querySelectorAll('[role="tab"]')).find(el=>el.innerText.includes('应收计划')).click()`)
  await waitFor(`Array.from(document.querySelectorAll('button')).some(el=>el.innerText.trim()==='登记回款')`)
  await evaluate(`Array.from(document.querySelectorAll('button')).find(el=>el.innerText.trim()==='登记回款').click()`)
  await waitFor(`document.querySelector('input[aria-label="实际到账金额"]')`)
  assert.equal(await evaluate(`document.querySelector('input[aria-label="实际到账金额"]').value`),'99999999999999.99')
  await evaluate(`Array.from(document.querySelectorAll('.semi-modal-content button')).find(el=>el.innerText.trim()==='提交财务确认').click()`)
  await waitFor(`!document.querySelector('input[aria-label="实际到账金额"]')`)
  assert.equal(financialPayments.length,1)
  assert.equal(financialPayments[0].received_amount,'99999999999999.99')
  console.log('BROWSER_REGISTER_PAYMENT_PRESERVES_UNEDITED_EXACT_AMOUNT=true')
  financialPlansEmpty=true
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/orders/991`})
  await waitFor(`Array.from(document.querySelectorAll('[role="tab"]')).some(el=>el.innerText.includes('应收计划'))`)
  await evaluate(`Array.from(document.querySelectorAll('[role="tab"]')).find(el=>el.innerText.includes('应收计划')).click()`)
  await waitFor(`Array.from(document.querySelectorAll('button')).some(el=>el.innerText.trim()==='生成应收计划')`)
  await evaluate(`Array.from(document.querySelectorAll('button')).find(el=>el.innerText.trim()==='生成应收计划').click()`)
  await waitFor(`document.querySelector('.semi-modal-content')?.innerText.includes('30,000,000,000,000.00')`)
  assert.ok(await evaluate(`document.querySelector('.semi-modal-content').innerText.includes('69,999,999,999,999.99')`))
  console.log('BROWSER_INSTALLMENT_PREVIEW_PRESERVES_EXACT_TOTAL_AND_TAIL=true')
  exactQuote=true
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/quotes`})
  await waitFor(`document.querySelector('.semi-table-tbody')?.innerText.includes('EXACT-QUOTE-121')`)
  assert.ok(await evaluate(`document.querySelector('.semi-table-tbody').innerText.includes('99,999,999,999,999.99')`))
  console.log('BROWSER_QUOTE_LIST_PRESERVES_EXACT_TOTAL=true')
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/customers/9?tab=quotes`})
  await waitFor(`document.querySelector('.semi-table-tbody')?.innerText.includes('EXACT-QUOTE-121')`)
  assert.ok(await evaluate(`document.querySelector('.semi-table-tbody').innerText.includes('99,999,999,999,999.99')`))
  console.log('BROWSER_CUSTOMER_QUOTE_LIST_PRESERVES_EXACT_TOTAL=true')
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/quotes/121?version=131`})
  // 「版本与方案对比 · 边际测算」2026-11-16 起收进折叠区、默认收起（报价详情去臃肿）。
  // 对比表与差额文案都在折叠区里，收起时页面读不到 —— 先点开面板标题再断言。
  await waitFor(`Array.from(document.querySelectorAll('.semi-collapse-header')).some(el=>el.innerText.includes('版本与方案对比'))`)
  await evaluate(`Array.from(document.querySelectorAll('.semi-collapse-header')).find(el=>el.innerText.includes('版本与方案对比')).click()`)
  await waitFor(`Array.from(document.querySelectorAll('.semi-table-tbody')).some(el=>el.innerText.includes('99,999,999,999,999.99'))`)
  console.log('BROWSER_QUOTE_COMPARISON_PRESERVES_EXACT_AMOUNT=true')
  await waitFor(`document.body.innerText.includes('金额 +0.01，毛利')`)
  console.log('BROWSER_QUOTE_COMPARISON_PRESERVES_CENT_DELTA=true')
  await waitFor(`document.body.innerText.includes('毛利 +0.01，毛利率')`)
  console.log('BROWSER_QUOTE_COMPARISON_PRESERVES_CENT_PROFIT_DELTA=true')
  await waitFor(`document.body.innerText.includes('99999999999999.98 → 99999999999999.99')`)
  console.log('BROWSER_QUOTE_COMPARISON_PRESERVES_EXACT_FEE_CHANGE=true')
  await waitFor(`document.body.innerText.includes('999999999999.9991 → 999999999999.9992') && document.body.innerText.includes('9999999999999.988 → 9999999999999.989')`)
  console.log('BROWSER_QUOTE_COMPARISON_PRESERVES_EXACT_ITEM_CHANGES=true')
  await waitFor(`Array.from(document.querySelectorAll('button')).some(el=>el.innerText.trim()==='转销售订单')`)
  await evaluate(`Array.from(document.querySelectorAll('button')).find(el=>el.innerText.trim()==='转销售订单').click()`)
  await waitFor(`document.querySelector('.semi-modal-content')?.innerText.includes('99,999,999,999,999.99')`)
  await evaluate(`Array.from(document.querySelectorAll('label,[role="radio"]')).find(el=>el.innerText.includes('分期收款')).click()`)
  await waitFor(`document.querySelector('.semi-modal-content')?.innerText.includes('30,000,000,000,000.00')`)
  assert.ok(await evaluate(`document.querySelector('.semi-modal-content').innerText.includes('69,999,999,999,999.99')`))
  console.log('BROWSER_QUOTE_CONVERT_PICKER_USES_EXACT_TOTAL_AND_INSTALLMENTS=true')
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/orders/994`})
  await waitFor(`document.querySelector('.semi-table-tbody')?.innerText.includes('FOUR-DECIMAL-ITEM')`)
  const exactItemRow=await evaluate(`document.querySelector('.semi-table-tbody').innerText`)
  assert.ok(exactItemRow.includes('¥33.3333'))
  assert.ok(exactItemRow.includes('¥33,333.30'))
  console.log('BROWSER_ORDER_ITEM_PRESERVES_FOUR_DECIMAL_UNIT_PRICE=true')







  analyticsExact=true
  // 「每月签单、发货与回款」在「明细报表 › 月度业绩」里；经营概览默认不渲染这张表。
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/analytics?view=reports&report=basis`})
  const annualExactRow=`Array.from(document.querySelectorAll('tr')).find(r=>r.innerText.startsWith('${analyticsFixture.period.slice(5)}\t'))`
  await waitFor(`${annualExactRow}?.innerText.includes('¥199,999,999,999,999')`)
  assert.equal(await evaluate(`(() => {
    const cell = Array.from(${annualExactRow}.querySelectorAll('td')).find(td => td.innerText === '¥199,999,999,999,999')
    return cell?.matches('.cell-money') && getComputedStyle(cell).whiteSpace === 'normal' && cell.scrollWidth <= cell.clientWidth
  })()`),true,'large amounts in the monthly report must fit without truncation')
  console.log('BROWSER_ANALYTICS_ANNUAL_TARGET_AND_PERSONNEL_EXACT_WHOLE_YUAN=true')

  // 目标卡按「对象类型」分开展示（全公司 / 部门 / 个人），不再是一张混排的表。
  const pickTargetScope=async label=>{
    // 报表筛选条里依次是「年份」「报表月份」「目标对象」，最后一个是对象类型。
    const scopeSelect=`Array.from(document.querySelectorAll('.analytics-report-filters .semi-select')).at(-1)`
    await waitFor(scopeSelect)
    await evaluate(`${scopeSelect}.click()`)
    const option=`Array.from(document.querySelectorAll('.semi-select-option')).find(o=>o.innerText.trim()===${JSON.stringify(label)})`
    await waitFor(option)
    await evaluate(`${option}.click()`)
    await waitFor(`${scopeSelect}.innerText.includes(${JSON.stringify(label)})`)
  }
  const openTargetEditor=async cardExpression=>{
    await waitFor(cardExpression)
    const edit=`Array.from(${cardExpression}.querySelectorAll('button')).find(b=>b.innerText.trim()==='修改目标')`
    await waitFor(edit)
    await evaluate(`${edit}.click()`)
    await waitFor(`document.querySelector('.semi-modal')?.innerText.includes('月度目标')`)
  }
  const clickTargetModalButton=label=>`Array.from(document.querySelector('.semi-modal').querySelectorAll('button')).find(b=>b.innerText.trim()===${JSON.stringify(label)}).click()`

  // 个人目标：整元精度同样不能四舍五入。
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/analytics?view=target`})
  // 销售目标默认只看当月；夹具目标是 1 月/3 月，先切「全部月份」。
  const targetMonthFilter=`Array.from(document.querySelectorAll('.analytics-report-filters .semi-select'))[1]`
  await waitFor(targetMonthFilter)
  await evaluate(`${targetMonthFilter}.click()`)
  await waitFor(`Array.from(document.querySelectorAll('.semi-select-option')).some(o=>o.innerText.trim()==='全部月份')`)
  await evaluate(`Array.from(document.querySelectorAll('.semi-select-option')).find(o=>o.innerText.trim()==='全部月份').click()`)
  await waitFor(`${targetMonthFilter}.innerText.includes('全部月份')`)
  await pickTargetScope('个人')
  const personTargetCard=`Array.from(document.querySelectorAll('.analytics-target-row')).find(r=>r.innerText.includes('隔离整元精度'))`
  await waitFor(`${personTargetCard}?.innerText.includes('¥199,999,999,999,999')`)
  assert.equal(await evaluate(`Boolean(${personTargetCard}?.innerText.includes('¥200,000,000,000,000'))`),false)

  // 团队目标：部门口径。
  await pickTargetScope('部门')
  const teamTargetCard=`Array.from(document.querySelectorAll('.analytics-target-row')).find(r=>r.innerText.includes('${analyticsFixture.team_target.name}'))`
  await openTargetEditor(teamTargetCard)
  assert.equal(analyticsTargetWrites.length,0,'opening a team target must not save')
  await evaluate(clickTargetModalButton('取消'))
  await sleep(100)
  assert.equal(analyticsTargetWrites.length,0,'cancel must not save')
  await openTargetEditor(teamTargetCard)
  await evaluate(clickTargetModalButton('保存目标'))
  for(let i=0;analyticsTargetWrites.length===0 && i<50;i++) await sleep(100)
  assert.equal(analyticsTargetWrites.length,1)
  assert.equal(analyticsTargetWrites[0].department_id,analyticsFixture.team_target.department_id)
  assert.equal(analyticsTargetWrites[0].user_id,null)
  assert.equal(analyticsTargetWrites[0].period,analyticsFixture.team_target.period)
  assert.equal(analyticsTargetWrites[0].expected_updated_at,analyticsFixture.team_target.expected_updated_at)
  assert.equal(analyticsTargetWrites[0].remark,analyticsFixture.team_target.remark)
  console.log('BROWSER_TEAM_TARGET_EDIT_KEEPS_DEPARTMENT_AND_CANCEL_DOES_NOT_SAVE=true')

  await waitFor(`!document.querySelector('.semi-modal')`)
  await pickTargetScope('个人')
  const moneyTargetCard=`Array.from(document.querySelectorAll('.analytics-target-row')).find(r=>r.innerText.includes('${analyticsFixture.money_target.name}'))`
  await openTargetEditor(moneyTargetCard)
  const targetSalesInput=`document.querySelector('input[aria-label="财务确认回款目标"]')`
  const targetRepeatInput=`document.querySelector('input[aria-label="老客户订单净额目标"]')`
  assert.deepEqual(await evaluate(`[${targetSalesInput}?.value,${targetRepeatInput}?.value]`),[analyticsFixture.money_target.amount,analyticsFixture.money_target.amount])
  const setTargetInput=async (input,value)=>{
    await evaluate(`${input}.focus();${input}.select()`)
    await send('Input.insertText',{text:value})
    await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
  }
  await setTargetInput(targetSalesInput,'abc')
  await evaluate(clickTargetModalButton('保存目标'))
  await waitFor(`document.body.innerText.includes('销售额目标只能填数字')`)
  assert.equal(analyticsTargetWrites.length,1,'invalid amount must not submit or become null')
  await setTargetInput(targetSalesInput,analyticsFixture.money_target.amount)
  // SemiModal确认按钮有leading-only的100ms防连点；输入改正后的重试应跨过该窗口。
  await sleep(120)
  await evaluate(clickTargetModalButton('保存目标'))
  for(let i=0;analyticsTargetWrites.length<2 && i<50;i++) await sleep(100)
  assert.equal(analyticsTargetWrites.length,2)
  const exactTargetPayload=analyticsTargetWrites[1]
  assert.equal(exactTargetPayload.sales_target,analyticsFixture.money_target.amount)
  assert.equal(exactTargetPayload.repeat_customer_target,analyticsFixture.money_target.amount)
  assert.equal(exactTargetPayload.user_id,analyticsFixture.money_target.user_id)
  assert.equal(exactTargetPayload.department_id,null)
  assert.equal(exactTargetPayload.expected_updated_at,analyticsFixture.money_target.expected_updated_at)
  assert.equal(exactTargetPayload.remark,analyticsFixture.money_target.remark)
  console.log('BROWSER_TARGET_EDIT_PRESERVES_EXACT_AMOUNTS_AND_REJECTS_INVALID_INPUT=true')


  await waitFor(`!document.querySelector('.semi-modal')`)
  rejectStaleTarget=true
  await openTargetEditor(moneyTargetCard)
  await setTargetInput(targetSalesInput,'123.45')
  await evaluate(clickTargetModalButton('保存目标'))
  await waitFor(`document.body.innerText.includes('刚被别人改过')`)
  assert.equal(analyticsTargetWrites.length,3)
  assert.equal(analyticsTargetWrites[2].expected_updated_at,analyticsFixture.money_target.expected_updated_at)
  assert.equal(analyticsTargetWrites[2].sales_target,'123.45')
  assert.equal(analyticsTargetWrites[2].remark,analyticsFixture.money_target.remark)
  assert.equal(await evaluate(`${targetSalesInput}.value`),'123.45','conflict must keep user input')
  assert.ok(await evaluate(`Boolean(document.querySelector('.semi-modal'))`),'conflict does not close editor')
  await evaluate(clickTargetModalButton('取消'))
  assert.equal(analyticsTargetWrites.length,3,'cancel after conflict does not overwrite')
  rejectStaleTarget=false
  console.log('BROWSER_TARGET_EDIT_SENDS_VERSION_AND_409_PRESERVES_INPUT=true')


  await waitFor(`!document.querySelector('.semi-modal')`)
  await openTargetEditor(moneyTargetCard)
  const targetMonthSelect=`Array.from(document.querySelectorAll('.semi-modal-body div')).find(el=>el.firstElementChild?.textContent.trim()==='月份')?.querySelector('.semi-select')`
  await evaluate(`${targetMonthSelect}.click()`)
  const originalMonth=Number(analyticsFixture.money_target.period.slice(5))
  const nextMonth=originalMonth===12 ? 1 : originalMonth+1
  const nextPeriod=analyticsFixture.money_target.period.slice(0,5)+String(nextMonth).padStart(2,'0')
  const nextMonthOption=`Array.from(document.querySelectorAll('.semi-select-option')).find(el=>el.innerText.trim()==='${analyticsFixture.money_target.period.slice(0,4)} 年 ${nextMonth} 月')`
  await waitFor(nextMonthOption)
  await evaluate(`${nextMonthOption}.click()`)
  await waitFor(`${targetMonthSelect}?.innerText.includes('${analyticsFixture.money_target.period.slice(0,4)} 年 ${nextMonth} 月')`)
  await sleep(120)
  await evaluate(clickTargetModalButton('保存目标'))
  for(let i=0;analyticsTargetWrites.length<4 && i<50;i++) await sleep(100)
  assert.equal(analyticsTargetWrites.length,4)
  assert.equal(analyticsTargetWrites[3].period,nextPeriod)
  assert.equal(analyticsTargetWrites[3].expected_updated_at,undefined,'different scope must not reuse previous row version')
  assert.equal(analyticsTargetWrites[3].remark,undefined,'different scope must not copy hidden remark')
  console.log('BROWSER_TARGET_CHANGED_PERIOD_DOES_NOT_REUSE_VERSION=true')
  console.log('BROWSER_TARGET_EDIT_PRESERVES_REMARK_WITHOUT_COPYING_TO_OTHER_PERIOD=true')

  {
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/orders/991?tab=shipments`})
  await waitFor(`Array.from(document.querySelectorAll('[role=tab]')).some(t=>t.innerText.includes('发货批次'))`)
  await evaluate(`Array.from(document.querySelectorAll('[role=tab]')).find(t=>t.innerText.includes('发货批次')).click()`)
  const open=`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='排发货批次')`
  const input=`document.querySelector('input[placeholder="本批数量"]')`
  const save=`Array.from(document.querySelectorAll('.semi-modal-content button')).find(b=>b.innerText.trim()==='保存批次')`
  await waitFor(open);await evaluate(`${open}.click()`);await waitFor(input)
  const fill=async()=>evaluate(`{const i=${input};Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,'4');i.dispatchEvent(new Event('input',{bubbles:true}))}`)
  await fill();await sleep(200);await evaluate(`${save}.click()`)
  await waitFor(`document.body.innerText.includes('隔离模拟：批次已保存但回执异常')`)
  assert.equal(shipmentBodies.length,1);assert.ok(shipmentBodies[0].request_key)
  assert.equal(await evaluate(`${input}.value`),'4')
  await sleep(200);await evaluate(`${save}.click()`)
  await waitFor(`!(${input})`)
  assert.equal(shipmentBodies.length,2);assert.deepEqual(shipmentBodies[0],shipmentBodies[1]);assert.equal(savedShipments.size,1)
  console.log('PASS failed create preserves input and same stable key on retry; sole simulated saved batch')
  await evaluate(`${open}.click()`);await waitFor(input);await fill();await sleep(200);await evaluate(`${save}.click()`)
  await waitFor(`!(${input})`)
  assert.equal(shipmentBodies.length,3);assert.notEqual(shipmentBodies[0].request_key,shipmentBodies[2].request_key);assert.equal(savedShipments.size,2)
  console.log('PASS explicit new form gets different key and creates next batch')
  assert.ok(shipmentBodies.every(body=>body.items[0].planned_qty==='4'),'ordinary quantities also submit as exact strings')
  shipmentLarge=true
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/orders/991`})
  await waitFor(`Array.from(document.querySelectorAll('[role=tab]')).some(t=>t.innerText.includes('发货批次'))`)
  await evaluate(`Array.from(document.querySelectorAll('[role=tab]')).find(t=>t.innerText.includes('发货批次')).click()`)
  await waitFor(`document.querySelector('.semi-table-tbody')?.innerText.includes('隔离旧批次 计划 9999999999999.999')`)
  await waitFor(open);await evaluate(`${open}.click()`);await waitFor(input)
  assert.match(await evaluate(`document.querySelector('.semi-modal-content').innerText`),/订购 9999999999999\.999 · 还可安排 9999999999999\.999/)
  await evaluate(`{const i=${input};Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,'9999999999999.999');i.dispatchEvent(new Event('input',{bubbles:true}))}`)
  await sleep(250);await evaluate(`${save}.click()`);await waitFor(`!(${input})`)
  assert.equal(shipmentBodies.at(-1).items[0].planned_qty,'9999999999999.999')
  console.log('PASS shipment maximum quantity keeps exact modal/batch display and submitted string')
  }
  {
  user.roles=['admin'];user.permissions=[]
  const draftAuth=JSON.stringify({state:{token:'isolated-local-only',user},version:0})
  await send('Page.addScriptToEvaluateOnNewDocument',{source:`localStorage.setItem('crm-auth',${JSON.stringify(draftAuth)})`})
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/order-drafts/993`})
  const qty=`document.querySelector('input[aria-label="草稿数量"]')`
  const price=`document.querySelector('input[aria-label="草稿单价"]')`
  await waitFor(qty)
  assert.equal(await evaluate(`${qty}.value`),'9999999999999.999')
  assert.equal(await evaluate(`${price}.value`),'0.0001')
  assert.equal(await evaluate(`document.querySelectorAll('.semi-input-number').length`),0)
  assert.ok(await evaluate(`document.body.innerText.includes('9,999,999,999,999.999')`))
  const setValue=async (selector,value,type='HTMLInputElement')=>evaluate(`{const e=${selector};Object.getOwnPropertyDescriptor(${type}.prototype,'value').set.call(e,${JSON.stringify(value)});e.dispatchEvent(new Event('input',{bubbles:true}))}`)
  const saveDraft=`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='保存草稿'&&!b.disabled)`
  // 「改完值站得住」才继续：保存的响应回填表单（setRows）可能落在下一笔编辑之后，
  // 把新值冲回旧值、第二笔按旧值提交（2026-11-16 两层偶发红的真根因）。
  // Semi 按钮的 loading 在 DOM 上毫无痕迹（类名被注释），区分不了「提交中」和「干净」，
  // 所以不猜状态：改完读回校验，被冲掉就再改，连续两次读到新值才算落定。
  const setStable=async (selector,value,type)=>{
    for(let i=0;i<20;i++){
      await setValue(selector,value,type)
      await sleep(150)
      if(await evaluate(`${selector}.value`)===value){await sleep(150);if(await evaluate(`${selector}.value`)===value)return}
    }
    throw Error('输入值被回填冲掉且重试无效：'+selector)
  }
  // 只改草稿表头备注（最后那个 textarea）；别碰页面外 AI 抽屉的输入框。
  const draftNote=`Array.from(document.querySelector('#order-draft-details')?.querySelectorAll('textarea') ?? []).at(-1)`
  await setStable(draftNote,'只改备注','HTMLTextAreaElement')
  await waitFor(saveDraft);await sleep(250);await evaluate(`${saveDraft}.click()`)
  // 断言请求体前先等记录落地，读到的才不是上一笔的旧值
  for(let i=0;draftPrecisionBodies.length<1 && i<30;i++)await sleep(100)
  assert.equal(draftPrecisionBodies.length,1)
  assert.equal(draftPrecisionBodies[0].items[0].quantity,'9999999999999.999')
  assert.equal(draftPrecisionBodies[0].items[0].unit_price,'0.0001')
  await setStable(qty,'0.001');await setStable(price,'999999999999.9999')
  await waitFor(saveDraft);await sleep(250);await evaluate(`${saveDraft}.click()`)
  for(let i=0;draftPrecisionBodies.length<2 && i<30;i++)await sleep(100)
  assert.equal(draftPrecisionBodies.at(-1).items[0].quantity,'0.001')
  assert.equal(draftPrecisionBodies.at(-1).items[0].unit_price,'999999999999.9999')
  await setStable(price,'');await waitFor(saveDraft);await sleep(250);await evaluate(`${saveDraft}.click()`)
  for(let i=0;draftPrecisionBodies.length<3 && i<30;i++)await sleep(100)
  assert.equal(draftPrecisionBodies.at(-1).items[0].unit_price,null)
  await setStable(price,'0');await waitFor(saveDraft);await sleep(250);await evaluate(`${saveDraft}.click()`)
  for(let i=0;draftPrecisionBodies.length<4 && i<30;i++)await sleep(100)
  assert.equal(draftPrecisionBodies.at(-1).items[0].unit_price,'0')
  assert.equal(draftPrecisionBodies.length,4)
  console.log('PASS order draft note-only save and edits preserve exact quantity/price; blank and zero distinct; no numeric steppers')
  }
  {
  exactQuote=true
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/quotes/121`})
  const fill=async(selector,value)=>evaluate(`{const e=${selector};Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,${JSON.stringify(value)});e.dispatchEvent(new Event('input',{bubbles:true}))}`)
  for(const mode of ['order','sample']) {
    const open=`Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()===${JSON.stringify(mode==='order'?'按此版本建订单草稿':'按此版本申请打样')})`
    await waitFor(open);await evaluate(`${open}.click()`)
    const qty=`document.querySelector('.semi-modal-content input[id^="source-quantity-${mode}-"]')`
    const price=`document.querySelector('.semi-modal-content input[aria-label="草稿单价"]')`
    const save=`Array.from(document.querySelectorAll('.semi-modal-content button')).find(b=>b.innerText.trim()===${JSON.stringify(mode==='order'?'建立订单草稿':'建立打样申请')}&&!b.disabled)`
    await waitFor(qty)
    assert.equal(await evaluate(`${qty}.value`),mode==='order'?'9999999999999.999':'1')
    assert.equal(await evaluate(`document.querySelectorAll('.semi-modal-content .semi-input-number').length`),0)
    assert.ok(await evaluate(`document.querySelector('.semi-modal-content').innerText.includes('9,999,999,999,999.999')`))
    assert.equal(await evaluate(`Boolean(${price})`),mode==='order')
    const submit=async()=>{const n=sourceCreateBodies[mode].length;await waitFor(save);await sleep(250);await evaluate(`${save}.click()`);for(let i=0;i<50&&sourceCreateBodies[mode].length===n;i++)await sleep(100);assert.equal(sourceCreateBodies[mode].length,n+1);await waitFor(`!document.querySelector('.semi-modal-content .semi-button-loading')`);await sleep(250)}
    await submit();await submit()
    assert.deepEqual(sourceCreateBodies[mode][0],sourceCreateBodies[mode][1])
    assert.equal(sourceCreateBodies[mode][0].items[0].quantity,mode==='order'?'9999999999999.999':'1')
    if(mode==='order') {
      assert.equal(sourceCreateBodies.order[0].items[0].unit_price,'0.0001')
      await fill(qty,'0.001');await fill(price,'999999999999.9999');await submit()
      assert.equal(sourceCreateBodies.order.at(-1).items[0].quantity,'0.001')
      assert.equal(sourceCreateBodies.order.at(-1).items[0].unit_price,'999999999999.9999')
      await fill(price,'');await submit();assert.equal(sourceCreateBodies.order.at(-1).items[0].unit_price,null)
      await fill(price,'0');await submit();assert.equal(sourceCreateBodies.order.at(-1).items[0].unit_price,'0')
    } else {
      assert.equal(sourceCreateBodies.sample[0].use_dingtalk,true)
      assert.equal('unit_price' in sourceCreateBodies.sample[0].items[0],false)
      await fill(qty,'9999999999999.999');await submit()
      assert.equal(sourceCreateBodies.sample.at(-1).items[0].quantity,'9999999999999.999')
      await fill(qty,'1.250');await submit();assert.equal(sourceCreateBodies.sample.at(-1).items[0].quantity,'1.250')
    }
    assert.notEqual(sourceCreateBodies[mode][1].request_key,sourceCreateBodies[mode][2].request_key)
    for(const invalid of ['', 'abc', '0', '-1']) {await fill(qty,invalid);await sleep(250);assert.equal(await evaluate(`Boolean(${save})`),false);assert.ok(await evaluate(`document.querySelector('.semi-modal-content').innerText.includes('必须大于 0')`))}
    const cancel=`Array.from(document.querySelectorAll('.semi-modal-content button')).find(b=>b.innerText.trim()==='取消')`
    await evaluate(`${cancel}.click()`);await waitFor(`!document.querySelector('.semi-modal-content')`)
    console.log('PASS source '+mode+' exact quantities, default, no steppers, stable failure retry and invalid quantity hints')
  }
  }
  analyticsExact=false
  failReceivables=true
  user.roles=['admin']
  await send('Page.navigate',{url:`http://127.0.0.1:${port}/analytics?view=payments`})
  const receivableKpi=`Array.from(document.querySelectorAll('.kpi-card')).find(card=>card.querySelector('.kpi-label')?.innerText.includes('应收合计'))`
  await waitFor(`${receivableKpi}?.innerText.includes('数据加载失败')`)
  assert.equal(await evaluate(`${receivableKpi}.querySelector('.kpi-value').innerText`),'—')
  failReceivables=false
  await evaluate(`${receivableKpi}.querySelector('.kpi-hint button, .chip button').click()`)
  await waitFor(`${receivableKpi}?.querySelector('.kpi-value').innerText==='¥0'`)
  console.log('BROWSER_ANALYTICS_FAILED_KPI_NEVER_REPORTS_ZERO_AND_RETRIES=true')
} finally {
  // 先让浏览器真的退出再删档案目录：没退干净就删会撞 ENOTEMPTY（它还在往里写），
  // 2026-11-16 就因此假红过一次（断言全过、收尾炸了）。清理失败**不能算测试失败**，
  // 口径同 smoke_ui：留个临时目录无害，别把真正的结论盖掉。
  socket?.close()
  if(browser){
    browser.kill('SIGTERM')
    const exited=await Promise.race([new Promise(r=>browser.once('exit',()=>r(true))),sleep(3000).then(()=>false)])
    if(!exited)browser.kill('SIGKILL')
  }
  server.closeAllConnections();server.close()
  for(let attempt=0;attempt<3;attempt++){
    try{rmSync(profile,{recursive:true,force:true});break}
    catch(error){
      if(attempt===2)console.log(`（提示：临时目录 ${profile} 没清掉，不影响本次测试结论：${error.code ?? error.message}）`)
      else await sleep(500)
    }
  }
}
