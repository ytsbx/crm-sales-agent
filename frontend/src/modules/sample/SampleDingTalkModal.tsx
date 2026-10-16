import OaFormSnapshot from '../../shared/components/OaFormSnapshot'
import SyncOaButton from '../../shared/components/SyncOaButton'
import type { ReactNode } from 'react'
import { useEffect, useState } from 'react'
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
  item_count: number
  fields: Array<{id:string;label:string;type:string;required:boolean;system:boolean;options:string[];format?:string}>
  draft: Partial<Draft>; candidates: Array<{id:number;label:string}>; records: OaApprovalInstance[]
}
const time = (value: string | null | undefined) => value && !Number.isNaN(new Date(value).getTime()) ? new Date(value).toLocaleString('zh-CN') : '尚未返回'

/** 表单里一格：**字段名在上、控件在下**，宽度由外层网格决定。
 *
 * 为什么要有这个件（主人 2026-10-16：「排版太丑了」）：这个弹窗原先是
 * "一行行加出来"的 —— 每行的控件宽度各写各的（190 / 150 / 100%），
 * 于是一屏里三种宽度、左边缘参差、下面还散着没有标签的裸输入框。
 * 统一走这个件之后：标签与控件成对、两列等宽、纵向间距一致。
 */
function Field({label,children,span}:{label:string;children:ReactNode;span?:boolean}) {
  return <div style={span?{gridColumn:'1 / -1'}:undefined}>
    <div style={{marginBottom:4,fontSize:13,color:'var(--crm-text-2)'}}>{label}</div>
    {children}
  </div>
}
export default function SampleDingTalkModal({sampleId,onClose}:{sampleId:number;onClose:()=>void}) {
  const client=useQueryClient()
  const query=useQuery({queryKey:['sample-dingtalk',sampleId],queryFn:()=>api.get<Options>(`/samples/${sampleId}/dingtalk`),refetchInterval:5000,refetchOnWindowFocus:true})
  const [edited,setEdited]=useState<Draft | null>(null)
  // 认领弹窗的目标与"同一次核定"的请求键（键由打开弹窗时生成，弹窗里的重试复用它）
  const [adoptTarget,setAdoptTarget]=useState<OaApprovalInstance | null>(null)
  const [adoptKey,setAdoptKey]=useState<string | null>(null)
  const draft=edited ?? {mode:query.data?.draft.mode ?? 'standalone',source_oa_id:query.data?.draft.source_oa_id,fields:query.data?.draft.fields ?? {}, resubmit:query.data?.draft.resubmit ?? false}
  const submitted=Boolean(query.data?.draft.submitted) && !edited?.resubmit

  // ---- 预计工时 → 本次打样积分：**自动同步**（2026-10-16 主人）----
  //
  // 主人原话：「打样积分＝工时 × 10 = — 分（与上面「本次打样积分」同一个口径，
  // 两边填一样就行）。**既然是一样的，你不会同步吗**」—— 对，让用户把同一个数
  // 填两遍是设计错了。
  //
  // 口径本来就是 ×10（申请单原件写着「用时 1 小时 = 10 积分」）：
  //   · 「本次打样积分」是**钉钉模板的必填控件**（真正提交给钉钉的是它）；
  //   · 「预计工时」是 CRM 这边的输入（顺带让附件那份 Excel 拿到工时）；
  //   · 所以填了工时 → **自动写进那个积分框**，用户不用管积分。
  //
  // 只有当积分框仍是那一版（或本来就是空的）时才覆盖 —— 用户自己填过的判断不抹掉。
  const pointsFieldId = query.data?.fields.find((f) => f.label === '本次打样积分')?.id
  const hoursInput = draft.fields['_estimated_hours'] ?? ''
  useEffect(() => {
    if (!pointsFieldId || submitted) return
    const raw = hoursInput.trim()
    const n = Number(raw)
    const next = raw && Number.isFinite(n) && n > 0 ? String(n * 10) : ''
    const current = draft.fields[pointsFieldId] ?? ''
    // 不再"保护手改过的值"：这两个本来就是**一个数**，永远跟着工时算
    if (current === next) return
    setEdited({ ...draft, fields: { ...draft.fields, [pointsFieldId]: next } })
    // `draft` 每次渲染都是新对象，放进依赖会无限循环；这里只跟这三个输入走。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hoursInput, pointsFieldId, submitted])

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
      {submitted && query.data?.records[0]?.allowed_actions?.includes('resubmit') && <Button disabled={mutate.isPending} onClick={()=>setEdited({...draft,resubmit:true})}>修改后重新提交</Button>}
      {submitted && draft.mode==='standalone' && query.data?.records[0]?.status==='pending' && query.data.records[0].instance_id &&
        <Button type="danger" disabled={mutate.isPending||withdraw.isPending} onClick={()=>Modal.confirm({title:'撤回钉钉打样审批？',content:'只对仍在审批中的钉钉流程生效。',onOk:()=>withdraw.mutate(query.data!.records[0].id)})}>撤回钉钉审批</Button>}
      {/* ⚠️ 这两个按钮**刻意不用** `disabled={!query.data}`（2026-10-15 修）：
          读不到模板时按钮会变灰，而用户看不出"连保存草稿都不行" —— 界面只有一个字
          都不说的灰按钮。改成**可点 + 如实报错**：后端对"模板未配置 / 推送关闭"
          有明确 4xx（`dingtalk.py:168`），点下去会看到原因；
          网络失败时 `mutate` 的 onError 也会弹出来。
          比"灰着不说为什么"好：**点得动才知道为什么**。 */}
      {!submitted&&<Button loading={mutate.isPending} onClick={()=>mutate.mutate(false)}>保存草稿</Button>}
      {(!submitted||query.data?.records[0]?.allowed_actions?.includes('retry'))&&<Button theme="solid"
        disabled={mutate.isPending}
        loading={mutate.isPending} onClick={()=>mutate.mutate(true)}>
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
      {query.data && draft.mode==='standalone' && query.data.item_count===0 && (
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',gap:12,
          padding:'10px 12px',border:'1px solid var(--crm-warning-border, #f0c36d)',
          background:'var(--crm-warning-bg, #fff8e6)',color:'var(--crm-text-2)'}}>
          <span>当前还没有打样明细；仍可提交钉钉审批，但自动生成的 Excel 中产品名称和数量会留空。如需完整申请表，请在样品详情的「样品明细」区域添加产品；本窗口下方的「产品名称」是申请表内容，不是明细行。</span>
          <Button size="small" onClick={onClose}>关闭并添加明细</Button>
        </div>
      )}
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
        {/* ⚠️ 「本次打样积分」**不在这里单独给输入框**（2026-10-16）：
            它与「预计工时 × 10」是**同一个数**。给两个输入框，
            用户填了工时、积分还是旧值 —— 主人原话：「打样积分不还是要填几遍吗」。
            现在它由工时算出来（见上面的同步 effect），界面上只在「预计工时」旁边显示算出来的分。 */}
        {/* 模板控件：**两列网格**，字段名在上、控件在下（与下面那组同一套排法）。 */}
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'10px 12px'}}>
          {query.data?.fields.filter(f=>!f.system && f.label!=='本次打样积分').map(field=>
            <Field key={field.id} span={!['DDSelectField','DDDateField','NumberField'].includes(field.type)}
              label={field.label + (field.required ? ' *' : '（选填）')}>
              {field.type==='DDSelectField'?<Select style={{width:'100%'}} placeholder='请选择' value={draft.fields[field.id]||undefined} disabled={submitted||mutate.isPending}
                optionList={field.options.map(value=>({value,label:value}))} onChange={value=>setEdited({...draft,fields:{...draft.fields,[field.id]:value as string}})}/>
              :field.type==='DDDateField'?<DatePicker type='date' format='yyyy-MM-dd' style={{width:'100%'}} value={draft.fields[field.id]||undefined} disabled={submitted||mutate.isPending}
                onChange={(_,value)=>setEdited({...draft,fields:{...draft.fields,[field.id]:(value as string)||''}})}/>
              :field.type==='NumberField'?<Input style={{width:'100%'}} value={draft.fields[field.id]||''} disabled={submitted||mutate.isPending} onChange={value=>setEdited({...draft,fields:{...draft.fields,[field.id]:value}})}/>
              :<TextArea rows={3} value={draft.fields[field.id]||''} disabled={submitted||mutate.isPending} onChange={value=>setEdited({...draft,fields:{...draft.fields,[field.id]:value}})}/>}
            </Field>)}
        </div>
        {/* 打样申请单上**该由业务员填的格**，一次排齐：两列网格、字段名在上。 */}
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'10px 12px',margin:'8px 0'}}>
          {([['sample_purpose','打样目的',['客审确认样','报价核算样','定制改款样','送检上架样']],
             ['urgency','紧急程度',['普通','急','紧急']],
             ['sample_situation','样品情况',['首次设计新品','设计更改','客户首次要求','客户更改','其他']]] as const).map(([key,label,options])=>
            <Field key={key} label={label}>
              <Select style={{width:'100%'}} placeholder="请选择" value={draft.fields['_'+key]||undefined}
                disabled={submitted||mutate.isPending}
                optionList={options.map(value=>({value,label:value}))}
                onChange={value=>setEdited({...draft,fields:{...draft.fields,['_'+key]:value as string}})}/>
            </Field>)}
          {(() => { const raw = draft.fields['_estimated_hours'] || ''; const n = Number(raw)
            const points = raw.trim() && Number.isFinite(n) ? n * 10 : null
            return <>
              <Field label="预计工时（小时）">
                <Input style={{width:'100%'}} value={raw} disabled={submitted||mutate.isPending}
                  onChange={value=>setEdited({...draft,fields:{...draft.fields,['_estimated_hours']:value}})}/>
              </Field>
              {points !== null && <Field label="打样积分">
                <div style={{height:32,lineHeight:'32px',fontSize:13,color:'var(--crm-text-2)'}}>{points} 分</div>
              </Field>}
            </> })()}
          <Field label="打样费用（元）">
            <Input style={{width:'100%'}} placeholder="留空表示未填；免费请填 0" value={draft.fields['_sample_fee']||''}
              disabled={submitted||mutate.isPending}
              onChange={value=>setEdited({...draft,fields:{...draft.fields,['_sample_fee']:value}})}/>
          </Field>
          <Field label="交样时间">
            <DatePicker type="date" format="yyyy-MM-dd" showClear style={{width:'100%'}}
              value={draft.fields['_deliver_date'] ? new Date(draft.fields['_deliver_date']) : undefined}
              disabled={submitted||mutate.isPending}
              onChange={(_,value)=>setEdited({...draft,fields:{...draft.fields,['_deliver_date']:(value as string)||''}})}/>
          </Field>
          <Field label="打样人">
            <Input style={{width:'100%'}} value={draft.fields['_sample_maker']||''} disabled={submitted||mutate.isPending}
              onChange={value=>setEdited({...draft,fields:{...draft.fields,['_sample_maker']:value}})}/>
          </Field>
          {([['material_spec','主要材质和规格'],['product_size','产品尺寸'],['printing','产品印刷'],['accessories','主要辅料'],['packaging','包装要求'],['craft_requirement','做法要求']] as const).map(([key,label])=>
            <Field key={key} label={label} span>
              <TextArea rows={2} value={draft.fields['_'+key]||''} disabled={submitted||mutate.isPending}
                onChange={value=>setEdited({...draft,fields:{...draft.fields,['_'+key]:value}})}/>
            </Field>)}
        </div>
        {/* 栏名照申请单原件那一格（主人 2026-10-16：「这个就改成产品图片（多方位图片）」）——
            这里上传的图片会**真的印在申请表「产品图片（多方位图片）」那一格上**（最多 4 张），
            其余文件（PDF 图纸、Excel…）作为钉钉附件随单传上去。 */}
        <div style={{fontWeight:600}}>产品图片（多方位图片）</div>
        {!submitted && <AttachmentPanel businessType="sample" businessId={sampleId} writePermission="sample:manage"
          category="sample_product_image" onlyCategory="sample_product_image" includeUncategorizedImages maxFiles={4}
          accept=".png,.jpg,.jpeg,.gif,.bmp,.webp,.tif,.tiff" uploadLabel="上传产品图片"
          hint="最多4张，仅放入Excel的四个产品图片框，不会作为独立附件传到钉钉。单个文件不超过20 MB"/>}
        {submitted && <div>已提交的资料已冻结，请在钉钉查看本轮附件。</div>}
      </>}
      <div style={{fontWeight:600}}>钉钉审批过程与结果</div>
      {query.data?.records.map(row=><div key={row.id} style={{border:'1px solid var(--crm-border)',padding:12}}>
        <div>第{row.submit_round??1}轮 · {row.status_label} · 最近同步：{time(row.synced_at)}</div>
        {row.instance_id&&<SyncOaButton oaId={row.id} invalidateKeys={[[ 'sample-dingtalk',sampleId ],['samples'],['sample',sampleId]]}/>}
        {row.sync_notice&&<div>{row.sync_notice}</div>}
        <OaFormSnapshot result={row.result}/>
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
