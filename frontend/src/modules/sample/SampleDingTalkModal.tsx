import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, DatePicker, Input, Modal, Select, Table, TextArea, Toast } from '@douyinfe/semi-ui'
import { api } from '../../shared/api/client'
import { type OaApprovalInstance, withdrawOaInstance } from '../../shared/api/dingtalk'
import { newRequestKey } from '../../shared/api/requestKey'
import { AdoptOaInstanceModal, ResolveOaActions } from '../../shared/components/ResolveOaActions'
import { approvalProgress } from '../knowledge/approval-progress'
import AttachmentPanel from '../common/AttachmentPanel'

interface Draft { mode: 'standalone' | 'inquiry'; source_oa_id?: number | null; fields: Record<string,string>; submitted?: boolean; resubmit?: boolean }
interface Options {
  configured: boolean; enabled: boolean
  fields: Array<{id:string;label:string;type:string;required:boolean;system:boolean;options:string[];format?:string}>
  draft: Partial<Draft>; candidates: Array<{id:number;label:string}>; records: OaApprovalInstance[]
}
const time = (value: string | null | undefined) => value && !Number.isNaN(new Date(value).getTime()) ? new Date(value).toLocaleString('zh-CN') : '尚未返回'
export default function SampleDingTalkModal({sampleId,onClose}:{sampleId:number;onClose:()=>void}) {
  const client=useQueryClient()
  const query=useQuery({queryKey:['sample-dingtalk',sampleId],queryFn:()=>api.get<Options>(`/samples/${sampleId}/dingtalk`),refetchInterval:30000})
  const [edited,setEdited]=useState<Draft | null>(null)
  // 认领弹窗的目标与"同一次核定"的请求键（键由打开弹窗时生成，弹窗里的重试复用它）
  const [adoptTarget,setAdoptTarget]=useState<OaApprovalInstance | null>(null)
  const [adoptKey,setAdoptKey]=useState<string | null>(null)
  const draft=edited ?? {mode:query.data?.draft.mode ?? 'standalone',source_oa_id:query.data?.draft.source_oa_id,fields:query.data?.draft.fields ?? {}, resubmit:query.data?.draft.resubmit ?? false}
  const submitted=Boolean(query.data?.draft.submitted) && !edited?.resubmit
  const mutate=useMutation({
    mutationFn:async(send:boolean)=>{
      if(send && draft.mode==='standalone') {
        if(!query.data?.configured||!query.data.enabled)throw new Error('独立打样模板未配置或推送关闭，请先保存草稿')
        const missing=query.data.fields.find(f=>!f.system&&f.required&&!draft.fields[f.id]?.trim())
        if(missing)throw new Error(`请填写${missing.label}`)
      }
      if(send&&draft.mode==='inquiry'&&!draft.source_oa_id)throw new Error('请选择原询价审批单')
      if(send)return api.post<OaApprovalInstance|null>(`/samples/${sampleId}/dingtalk-submit`,draft)
      return api.put<null>(`/samples/${sampleId}/dingtalk-draft`,draft)
    },
    onSuccess:(result,send)=>{
      if(!send)Toast.success('草稿已保存，未发出钉钉审批')
      else if(draft.mode==='inquiry')Toast.success('已关联原询价；请在原钉钉单继续打样，没有新发审批')
      else if(result?.status==='pending'&&result.instance_id)Toast.success('已提交钉钉打样审批')
      else Toast.warning('请查看审批状态；结果不明时先核对钉钉，不要重复发起')
      setEdited(null)
      void query.refetch();void client.invalidateQueries({queryKey:['samples']});void client.invalidateQueries({queryKey:['sample',sampleId]})
    },onError:(error:Error)=>Toast.error(error.message),
  })
  const withdraw=useMutation({
    mutationFn:(oaId:number)=>withdrawOaInstance(oaId),
    onSuccess:()=>{Toast.success('已撤回钉钉打样审批');void query.refetch()},
    onError:(error:Error)=>Toast.error(error.message),
  })
  return <Modal visible title="打样钉钉流程" width={900} bodyStyle={{maxHeight:'70vh',overflowY:'auto'}}
    onCancel={()=>{if(!mutate.isPending)onClose()}}
    footer={<div style={{display:'flex',justifyContent:'flex-end',gap:10}}>
      <Button disabled={mutate.isPending} onClick={onClose}>关闭</Button>
      {submitted && query.data?.records[0]?.status==='rejected' && <Button disabled={mutate.isPending} onClick={()=>setEdited({...draft,resubmit:true})}>修改后重新提交</Button>}
      {submitted && draft.mode==='standalone' && query.data?.records[0]?.status==='pending' && query.data.records[0].instance_id &&
        <Button type="danger" disabled={mutate.isPending||withdraw.isPending} onClick={()=>Modal.confirm({title:'撤回钉钉打样审批？',content:'只对仍在审批中的钉钉流程生效。',onOk:()=>withdraw.mutate(query.data!.records[0].id)})}>撤回钉钉审批</Button>}
      {/* ⚠️ 这两个按钮**刻意不用** `disabled={!query.data}`（2026-10-15 修）：
          读不到模板时按钮会变灰，而用户看不出"连保存草稿都不行" —— 界面只有一个字
          都不说的灰按钮。改成**可点 + 如实报错**：后端对"模板未配置 / 推送关闭"
          有明确 4xx（`dingtalk.py:168`），点下去会看到原因；
          网络失败时 `mutate` 的 onError 也会弹出来。
          比"灰着不说为什么"好：**点得动才知道为什么**。 */}
      {!submitted&&<Button loading={mutate.isPending} onClick={()=>mutate.mutate(false)}>保存草稿</Button>}
      {(!submitted||query.data?.records[0]?.allowed_actions?.includes('retry'))&&<Button theme="solid" loading={mutate.isPending} onClick={()=>mutate.mutate(true)}>
        {submitted?'重试原申请':draft.mode==='inquiry'?'关联原询价（不发新单）':'提交打样到钉钉'}
      </Button>}
    </div>}>
    {/* 请求失败：**必须说清后果**。原来只印一句 `query.error.message`，
        用户看到红的但不知道"下面这些格子和按钮全都不能用、也不能保存"，
        只会反复点那几个灰按钮。 */}
    {query.isError&&<div style={{color:'var(--crm-error)'}}>
      {`${query.error.message}。下面的流程配置读不出来，此时不能保存草稿、也不能提交；请点「重试」或关掉重开。`}
    </div>}
    <div style={{display:'grid',gridTemplateColumns:'minmax(0,1fr)',gap:12}}>
      <div>生产部在钉钉处理，CRM每约3分钟同步结果和过程。保存草稿不会发出审批。</div>
      {/* 后端 template() 对"未配置/推送关闭"给出 `configured=false` 或 `enabled=false`；
          它的 message（「独立打样模板未配置…」「钉钉推送未开启」）就在下面的红字里。
          这里补一句原因，免得那句业务话被当成又一条技术错误。 */}
      {!query.isError && query.data && (!query.data.configured || !query.data.enabled) &&
        <div style={{color:'var(--crm-hint, var(--crm-text-3))'}}>
          {query.data.configured
            ? '钉钉推送当前是关闭的，独立发起会失败；可以保存草稿。'
            : '独立打样审批模板还没配置，独立发起会失败；「沿用原询价流程」仍可用。'}
        </div>}
      <Select value={draft.mode} disabled={submitted||mutate.isPending} style={{width:'100%'}}
        optionList={[{value:'standalone',label:'单独发起打样审批'},{value:'inquiry',label:'沿用原询价流程（不另发审批）'}]}
        onChange={value=>setEdited({...draft,mode:value as Draft['mode'],source_oa_id:null})}/>
      {draft.mode==='inquiry'?<>
        <div>仅能关联这张样品真实来源询价的审批。后续打样步骤由人员在原钉钉单继续办理。</div>
        <Select style={{width:'100%'}} placeholder="选择原询价钉钉审批" value={draft.source_oa_id??undefined} disabled={submitted||mutate.isPending}
          optionList={(query.data?.candidates??[]).map(c=>({value:c.id,label:c.label}))}
          onChange={value=>setEdited({...draft,source_oa_id:value as number})}/>
        {query.data&&!query.data.candidates.length&&<div>没有可关联的来源审批。请先把此样品关联真实的询价来源，或选择单独申请；系统不会随便关联同客户的其他单。</div>}
      </>:<>
        {query.data&&!query.data.configured&&<div>独立打样模板尚未配置，可先保存草稿，请联系管理员。</div>}
        {/* 两组字段要分开写清楚（2026-10-16 主人：「随审批上传的申请表内容 这一段
            直接改成打样申请单不就行了」）。核对过钉钉模板的 13 个控件：**没有**"产品名称 /
            主要材质和规格…"这 7 项 —— 它们不是钉钉表单的格，而是**写进附件那份 Excel 的**。
            所以这里是两组不同的东西，原来的问题只是"第一组没标题、第二组标题又长又绕"，
            看起来像一团。现在两组各有标题、各说清去哪。 */}
        <div style={{fontWeight:600}}>钉钉审批单（下面这些填进钉钉表单）</div>
        <div>申请人和部门由当前账号绑定的钉钉身份自动填写；申请编号由钉钉生成；样品明细表由CRM按现有公司底稿生成并随审批上传。</div>
        {query.data?.fields.filter(f=>!f.system).map(field=><div key={field.id}>
          <div style={{marginBottom:4}}>{field.label}{field.required?' *':'（选填）'}</div>
          {field.type==='DDSelectField'?<Select style={{width:'100%'}} value={draft.fields[field.id]||undefined} disabled={submitted||mutate.isPending}
            optionList={field.options.map(value=>({value,label:value}))} onChange={value=>setEdited({...draft,fields:{...draft.fields,[field.id]:value as string}})}/>
          :field.type==='DDDateField'?<DatePicker type="date" format="yyyy-MM-dd" style={{width:'100%'}} value={draft.fields[field.id]||undefined} disabled={submitted||mutate.isPending}
            onChange={(_,value)=>setEdited({...draft,fields:{...draft.fields,[field.id]:(value as string)||''}})}/>
          :field.type==='NumberField'?<Input value={draft.fields[field.id]||''} disabled={submitted||mutate.isPending} placeholder="填0到99990之间的积分" onChange={value=>setEdited({...draft,fields:{...draft.fields,[field.id]:value}})}/>
          :<TextArea rows={3} value={draft.fields[field.id]||''} disabled={submitted||mutate.isPending} onChange={value=>setEdited({...draft,fields:{...draft.fields,[field.id]:value}})}/>}
        </div>)}
        <div style={{fontWeight:600}}>打样申请单（下面这些写进随审批上传的那份 Excel 附件）</div>
        {/* 三个**勾选项**（2026-10-16 补）：申请表上的「打样目的 / 紧急程度 / 样品情况」
            三格。它们**不在钉钉模板的 13 个控件里**（核对过），所以只能在这里填 ——
            以前只在详情页的「生产打样资料」里能填，而本弹窗是建完单立刻自动弹出来的，
            用户看到的是空格子却无处可填（主人：「为什么不让我填」）。
            选项照抄申请单原件的勾选项，不要自造。 */}
        <div style={{display:'flex',gap:8,flexWrap:'wrap'}}>
          {([['sample_purpose','打样目的',['客审确认样','报价核算样','定制改款样','送检上架样']],
             ['urgency','紧急程度',['普通','急','紧急']],
             ['sample_situation','样品情况',['首次设计新品','设计更改','客户首次要求','客户更改','其他']]] as const).map(([key,label,options])=>
            <Select key={key} style={{width:190}} placeholder={label} value={draft.fields['_'+key]||undefined}
              disabled={submitted||mutate.isPending}
              optionList={options.map(value=>({value,label:value}))}
              onChange={value=>setEdited({...draft,fields:{...draft.fields,['_'+key]:value as string}})}/>)}
        </div>
        {/* 预计工时 → 打样积分（申请单上那格「本次打样积分」＝工时×10）。
            钉钉模板里有「本次打样积分」控件，提交时会带过去；这里让 CRM 也能算一遍，
            两边口径同一个（×10），所以数值一致。 */}
        <div style={{display:'flex',gap:8,alignItems:'center'}}>
          <Input style={{width:150}} placeholder="预计工时（小时）" value={draft.fields['_estimated_hours']||''}
            disabled={submitted||mutate.isPending}
            onChange={value=>setEdited({...draft,fields:{...draft.fields,['_estimated_hours']:value}})}/>
          <span style={{fontSize:12,color:'var(--crm-text-3)'}}>
            打样积分＝工时 × 10 =
            <b>{(() => { const raw = draft.fields['_estimated_hours'] || ''; const n = Number(raw)
              return raw.trim() && Number.isFinite(n) ? ` ${n * 10} ` : ' — ' })()}</b>
            分（与上面「本次打样积分」同一个口径，两边填一样就行）
          </span>
        </div>
        {([['product_name','产品名称'],['material_spec','主要材质和规格'],['product_size','产品尺寸'],['printing','产品印刷'],['accessories','主要辅料'],['packaging','包装要求'],['craft_requirement','做法要求']] as const).map(([key,label])=><div key={key}>
          <div>{label}</div><TextArea rows={2} value={draft.fields['_'+key]||''} disabled={submitted||mutate.isPending}
            onChange={value=>setEdited({...draft,fields:{...draft.fields,['_'+key]:value}})}/>
        </div>)}
        <div style={{fontWeight:600}}>图纸、照片和其他制作资料（保存到当前打样单）</div>
        {!submitted && <AttachmentPanel businessType="sample" businessId={sampleId} writePermission="sample:manage"/>}
        {submitted && <div>已提交的资料已冻结，请在钉钉查看本轮附件。</div>}
      </>}
      <div style={{fontWeight:600}}>钉钉审批过程与结果</div>
      {query.data?.records.map(row=><div key={row.id} style={{border:'1px solid var(--crm-border)',padding:12}}>
        <div>第{row.submit_round??1}轮 · {row.status_label} · 最近同步：{time(row.synced_at)}</div>
        {row.error&&<div style={{color:'var(--crm-error)'}}>{row.error}</div>}
        <Table dataSource={approvalProgress(row.result).map((r,index)=>({...r,key:index}))} rowKey="key" pagination={false} scroll={{x:700}}
          columns={[{title:'处理人',dataIndex:'person',width:180},{title:'节点/操作',dataIndex:'node',width:150},{title:'状态',dataIndex:'status',width:130},
            {title:'时间',dataIndex:'time',width:180,render:time},{title:'处理意见',dataIndex:'remark'}]}/>
        {/* 「结果待人工核对」的三个出口（认领/重发/作废）。按钮由服务端的
            allowed_actions 决定 —— 打样这条链原先一个都没有，超时后单据彻底卡死。 */}
        <div style={{marginTop:8,display:'flex',gap:12}}>
          <ResolveOaActions row={row} invalidateKeys={[['sample-dingtalk',sampleId],['samples'],['sample',sampleId]]}
            onAdopt={target=>{setAdoptTarget(target);setAdoptKey(newRequestKey())}}
            onDone={()=>void query.refetch()}/>
        </div>
      </div>)}
      {query.data&&!query.data.records.length&&<div>{submitted?'尚未返回可查看的审批记录，请核对原钉钉单或稍后刷新。':'尚未提交或关联钉钉审批。'}</div>}
      <AdoptOaInstanceModal target={adoptTarget} requestKey={adoptKey}
        invalidateKeys={[['sample-dingtalk',sampleId],['samples'],['sample',sampleId]]}
        onClose={()=>{setAdoptTarget(null);setAdoptKey(null)}}/>
    </div>
  </Modal>
}
