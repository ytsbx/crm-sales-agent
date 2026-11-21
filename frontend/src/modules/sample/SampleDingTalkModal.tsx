import OaFormSnapshot from '../../shared/components/OaFormSnapshot'
import AutoOaSync from '../../shared/components/AutoOaSync'
import SyncOaButton from '../../shared/components/SyncOaButton'
import type { ReactNode } from 'react'
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, DatePicker, Input, Modal, Select, Table, TextArea, Toast } from '@douyinfe/semi-ui'
import { api } from '../../shared/api/client'
import { type OaApprovalInstance, withdrawOaInstance } from '../../shared/api/dingtalk'
import { newRequestKey } from '../../shared/api/requestKey'
import { AdoptOaInstanceModal, ResolveOaActions } from '../../shared/components/ResolveOaActions'
import { approvalProgress, formatApprovalTime, formatApprovalEventTime } from '../knowledge/approval-progress'
import AttachmentPanel from '../common/AttachmentPanel'
import FormLabel from '../../shared/components/FormLabel'

interface Draft { mode: 'standalone' | 'inquiry'; source_oa_id?: number | null; fields: Record<string,string>; submitted?: boolean; resubmit?: boolean }
interface Options {
  configured: boolean; enabled: boolean
  item_count: number
  native_form?: boolean
  template_error?: string
  prefill?: { applicant_name: string; department: string }
  fields: Array<{id:string;label:string;type:string;required:boolean;system:boolean;options:string[];format?:string;hint?:string;help_text?:string}>
  draft: Partial<Draft> & { requires_self_oa?: boolean }; candidates: Array<{id:number;label:string}>; records: OaApprovalInstance[]
}
const time = formatApprovalTime

/** 表单里一格：**字段名在上、控件在下**，宽度由外层网格决定。
 *
 * 为什么要有这个件（主人 2026-10-16：「排版太丑了」）：这个弹窗原先是
 * "一行行加出来"的 —— 每行的控件宽度各写各的（190 / 150 / 100%），
 * 于是一屏里三种宽度、左边缘参差、下面还散着没有标签的裸输入框。
 * 统一走这个件之后：标签与控件成对、两列等宽、纵向间距一致。
 */
function Field({label,children,span,required,hint}:{label:string;children:ReactNode;span?:boolean;required?:boolean;hint?:string}) {
  return <div style={span?{gridColumn:'1 / -1'}:undefined}>
    <FormLabel required={required} hint={hint} style={{fontSize:13,color:'var(--crm-text-2)'}}>{label}</FormLabel>
    {children}
  </div>
}
export default function SampleDingTalkModal({sampleId,onClose}:{sampleId:number;onClose:()=>void}) {
  const client=useQueryClient()
  const query=useQuery({queryKey:['sample-dingtalk',sampleId],queryFn:()=>api.get<Options>(`/samples/${sampleId}/dingtalk`),refetchInterval:5000,refetchOnWindowFocus:true})
  const [edited,setEdited]=useState<Draft | null>(null)
  // 认领弹窗的目标与"同一次核定"的请求键（键由打开弹窗时生成，弹窗里的重试复用它）
  const [adoptTarget,setAdoptTarget]=useState<OaApprovalInstance | null>(null)
  // 工时没填时给出"能照着做"的提示，并把光标定位到工时框（2026-11-18）。
  // 原来直接提交，后端按模板必填校验报「请填写本次打样积分」——
  // 页面上根本没有积分输入框（积分是工时算出来的），用户照着提示找不到东西。
  const [hoursMissing,setHoursMissing]=useState(false)
  const [adoptKey,setAdoptKey]=useState<string | null>(null)
  const draft=edited ?? {mode:query.data?.draft.mode ?? 'standalone',source_oa_id:query.data?.draft.source_oa_id,fields:query.data?.draft.fields ?? {}, resubmit:query.data?.draft.resubmit ?? false}
  const submitted=Boolean(query.data?.draft.submitted) && !edited?.resubmit
  const requiresSelfOa = query.data?.draft.requires_self_oa
  const approval = query.data?.records[0]
  let nextStep = '填好这版打样资料后提交同序 OA。暂时没填完，可以先保存草稿。'
  if (draft.mode === 'inquiry') {
    nextStep = submitted
      ? '已关联原询价。请到原同序 OA 单继续办理，之后可回样品详情查看进度。'
      : '选中本单的原询价审批后点「关联原询价」。后续在原同序 OA 单办理。'
  } else if (submitted) {
    if (approval?.status === 'approved') nextStep = '审批已通过。返回样品详情，在车间实际做完后登记制作完成。'
    else if (approval?.status === 'pending') nextStep = '正在等同序 OA 审批。可以关闭弹窗，之后从样品详情回来查看进度。'
    else if (approval?.status === 'rejected') nextStep = '先查看驳回意见。处理后，如有「修改后重新提交」按钮，可从这里继续办理。'
    else if (approval?.status === 'needs_review' || approval?.status === 'submitting') {
      nextStep = '请先在同序 OA 核对审批是否已收到，确认结果后再继续操作。'
    } else nextStep = '请先查看下方审批记录，再按当前可用的按钮处理。'
  }

  // ---- 预计工时 → 本次打样积分：**自动同步**（2026-10-16 主人）----
  //
  // 主人原话：「打样积分＝工时 × 10 = — 分（与上面「本次打样积分」同一个口径，
  // 两边填一样就行）。**既然是一样的，你不会同步吗**」—— 对，让用户把同一个数
  // 填两遍是设计错了。
  //
  // 口径本来就是 ×10（申请单原件写着「用时 1 小时 = 10 积分」）：
  //   · 「本次打样积分」是**同序 OA 模板的必填控件**（真正提交给同序 OA 的是它）；
  //   · 「预计工时」是 CRM 这边的输入（顺带让附件那份 Excel 拿到工时）；
  //   · 所以填了工时 → **自动写进那个积分框**，用户不用管积分。
  //
  // 只有当积分框仍是那一版（或本来就是空的）时才覆盖 —— 用户自己填过的判断不抹掉。
  const pointsFieldId = query.data?.native_form ? undefined : query.data?.fields.find((f) => f.label === '本次打样积分')?.id
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
        if(!query.data?.configured||!query.data.enabled)throw new Error(query.data?.template_error || '暂不能发起同序打样，可先保存草稿')
        // 通用必填先按顺序挡（**排除积分那一格**：页面上没有它的输入框）。
        const missing=query.data.fields.find(f=>!f.system&&f.required&&f.id!==pointsFieldId&&!draft.fields[f.id]?.trim())
        if(missing)throw new Error(`请填写${missing.label}`)
        // 积分是派生的（工时 × 10），页面上只有工时框：轮到积分时要点名**工时**，
        // 别让后端那句「请填写本次打样积分」指着一个界面上不存在的格子。
        if(pointsFieldId && !hoursInput.trim()) {
          setHoursMissing(true)
          requestAnimationFrame(()=>{
            const el=document.querySelector<HTMLInputElement>('.sample-hours-input input')
            el?.scrollIntoView({behavior:'smooth',block:'center'})
            el?.focus()
          })
          throw new Error('请填写预计工时，系统会自动换算打样积分')
        }
        setHoursMissing(false)
      }
      if(send&&draft.mode==='inquiry'&&!draft.source_oa_id)throw new Error('请选择原询价审批单')
      if(send)return api.post<OaApprovalInstance|null>(`/samples/${sampleId}/dingtalk-submit`,draft)
      return api.put<null>(`/samples/${sampleId}/dingtalk-draft`,draft)
    },
    onSuccess:(result,send)=>{
      if(!send)Toast.success('草稿已保存。填齐资料后，可从样品详情回来提交。')
      else if(draft.mode==='inquiry')Toast.success('已关联原询价；请在原同序 OA 单继续打样，没有新发审批')
      else if(result?.status==='pending'&&result.instance_id)Toast.success('已提交同序 OA 审批，可回样品详情查看进度。')
      else if(result?.status==='approved')Toast.success('审批已通过，可回样品详情登记制作完成。')
      else Toast.warning('请查看审批状态；结果不明时先核对同序 OA，不要重复发起')
      setEdited(null)
      void query.refetch();void client.invalidateQueries({queryKey:['samples']});void client.invalidateQueries({queryKey:['sample-detail',sampleId]})
    },onError:(error:Error)=>Toast.error(error.message),
  })
  const withdraw=useMutation({
    mutationFn:(oaId:number)=>withdrawOaInstance(oaId),
    onSuccess:()=>{Toast.success('已撤回同序 OA 打样审批');void query.refetch()},
    onError:(error:Error)=>Toast.error(error.message),
  })
  return <Modal visible title="同序 OA 打样流程" width={900} style={{maxWidth:'calc(100vw - 32px)'}} bodyStyle={{maxHeight:'70vh',overflowY:'auto'}}
    onCancel={()=>{if(!mutate.isPending)onClose()}}
    footer={<div style={{display:'flex',flexWrap:'wrap',justifyContent:'flex-end',gap:10}}>
      <Button disabled={mutate.isPending} onClick={onClose}>关闭</Button>
      {submitted && query.data?.records[0]?.allowed_actions?.includes('resubmit') && <Button disabled={mutate.isPending} onClick={()=>setEdited({...draft,resubmit:true})}>修改后重新提交</Button>}
      {submitted && draft.mode==='standalone' && query.data?.records[0]?.status==='pending' && query.data.records[0].instance_id &&
        <Button type="danger" disabled={mutate.isPending||withdraw.isPending} onClick={()=>Modal.confirm({title:'撤回同序 OA 打样审批？',content:'只对仍在审批中的同序 OA 流程生效。',onOk:()=>withdraw.mutate(query.data!.records[0].id)})}>撤回同序 OA 审批</Button>}
      {!submitted&&<Button disabled={!query.data} loading={mutate.isPending} onClick={()=>mutate.mutate(false)}>保存草稿</Button>}
      {(!submitted||query.data?.records[0]?.allowed_actions?.includes('retry'))&&<Button theme="solid"
        disabled={!query.data || mutate.isPending}
        loading={mutate.isPending} onClick={()=>mutate.mutate(true)}>
        {submitted?'重试原申请':draft.mode==='inquiry'?'关联原询价（不发新单）':'提交打样到同序 OA'}
      </Button>}
    </div>}>
    {query.isLoading && <p className="workflow-guide">正在读取打样资料…</p>}
    {query.isError&&<div role="alert" style={{color:'var(--crm-error)'}}>
      <p className="workflow-guide">{query.data ? '暂时读不到最新审批状态。' : '审批资料未能加载，请重新读取后再操作。'}</p>
      <Button size="small" onClick={()=>void query.refetch()}>重新读取审批资料</Button>
    </div>}
    <div style={{display:'grid',gridTemplateColumns:'minmax(0,1fr)',gap:12}}>
      {query.data && <p className="workflow-guide">{nextStep}</p>}
      {query.data && draft.mode==='standalone' && query.data.item_count===0 && (
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',gap:12,
          padding:'10px 12px',border:'1px solid var(--crm-warning-border)',
          background:'var(--crm-warning-bg)',color:'var(--crm-text-2)'}}>
          <span>当前还没有打样明细；仍可提交同序 OA 审批，但自动生成的 Excel 中产品名称和数量会留空。如需完整申请表，请在样品详情的「样品明细」区域添加产品；本窗口下方的「产品名称」是申请表内容，不是明细行。</span>
          <Button size="small" onClick={onClose}>关闭并添加明细</Button>
        </div>
      )}
      {/* 后端 template() 对"未配置/推送关闭"给出 `configured=false` 或 `enabled=false`；
          它的 message（「独立打样模板未配置…」「同序 OA 推送未开启」）就在下面的红字里。
          这里补一句原因，免得那句业务话被当成又一条技术错误。 */}
      {query.data?.template_error && <div role="alert">{query.data.template_error}</div>}
      {!query.isError && query.data && !query.data.template_error && (!query.data.configured || !query.data.enabled) &&
        <div style={{color:'var(--crm-hint, var(--crm-text-3))'}}>
          {requiresSelfOa
            ? '此修订版须单独提交同序 OA。打样模板尚未配置，可以先保存草稿。'
            : query.data.configured
            ? '同序 OA 推送当前是关闭的，独立发起会失败；可以保存草稿。'
            : '独立打样审批模板还没配置，独立发起会失败；「沿用原询价流程」仍可用。'}
        </div>}
      {requiresSelfOa && query.data?.configured && <div>此修订版将单独提交同序 OA 审批。</div>}
      {!requiresSelfOa && <Select value={draft.mode} disabled={submitted||mutate.isPending} style={{width:'100%'}}
        optionList={[{value:'standalone',label:'单独发起打样审批'},{value:'inquiry',label:'沿用原询价流程（不另发审批）'}]}
        onChange={value=>setEdited({...draft,mode:value as Draft['mode'],source_oa_id:null})}/>}
      {draft.mode==='inquiry'?<>
        <div>仅能关联这张样品真实来源询价的审批。后续打样步骤由人员在原同序 OA 单继续办理。</div>
        <Select style={{width:'100%'}} placeholder="选择原询价同序 OA 审批" value={draft.source_oa_id??undefined} disabled={submitted||mutate.isPending}
          optionList={(query.data?.candidates??[]).map(c=>({value:c.id,label:c.label}))}
          onChange={value=>setEdited({...draft,source_oa_id:value as number})}/>
        {query.data&&!query.data.candidates.length&&<div>没有可关联的来源审批。请先把此样品关联真实的询价来源，或选择单独申请；系统不会随便关联同客户的其他单。</div>}
      </>:<>
        {query.data&&!query.data.configured&&<div>独立打样模板尚未配置，可先保存草稿，请联系管理员。</div>}
        {/* 两组字段要分开写清楚（2026-10-16 主人：「随审批上传的申请表内容 这一段
            直接改成打样申请单不就行了」）。核对过同序 OA 模板的 13 个控件：**没有**"产品名称 /
            主要材质和规格…"这 7 项 —— 它们不是同序 OA 表单的格，而是**写进附件那份 Excel 的**。
            所以这里是两组不同的东西，原来的问题只是"第一组没标题、第二组标题又长又绕"，
            看起来像一团。现在两组各有标题、各说清去哪。 */}
        <div style={{fontWeight:600}}>同序 OA 审批表单</div>
        {/* ⚠️ 「本次打样积分」**不在这里单独给输入框**（2026-10-16）：
            它与「预计工时 × 10」是**同一个数**。给两个输入框，
            用户填了工时、积分还是旧值 —— 主人原话：「打样积分不还是要填几遍吗」。
            现在它由工时算出来（见上面的同步 effect），界面上只在「预计工时」旁边显示算出来的分。 */}
        {/* 模板控件：**两列网格**，字段名在上、控件在下（与下面那组同一套排法）。 */}
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'10px 12px'}}>
          {query.data?.fields.filter(f=>query.data?.native_form || (!f.system && f.label!=='本次打样积分')).map(field=>
            <Field key={field.id} span={!['DDSelectField','DDDateField','NumberField'].includes(field.type)}
              label={field.label} required={field.required} hint={field.help_text}>
              {field.type==='description' ? <p style={{whiteSpace:'pre-line'}}>{field.hint}</p>
              :field.type==='serial' ? <Input readOnly placeholder={submitted ? "编号可在同序查看" : "由同序自动编号"}/>
              :field.id==='applicant_name' || field.id==='department' ? <Input readOnly value={query.data?.prefill?.[field.id] ?? ''}/>
              :field.id==='sample_items' ? <div>提交时随单上传 CRM 打样明细 Excel（当前 {query.data?.item_count ?? 0} 条）</div>
              :field.type==='DDSelectField'?<Select style={{width:'100%'}} placeholder='请选择' value={draft.fields[field.id]||undefined} disabled={submitted||mutate.isPending}
                optionList={field.options.map(value=>({value,label:value}))} onChange={value=>setEdited({...draft,fields:{...draft.fields,[field.id]:value as string}})}/>
              :field.type==='DDDateField'?<DatePicker type='date' format='yyyy-MM-dd' style={{width:'100%'}} value={draft.fields[field.id]||undefined} disabled={submitted||mutate.isPending}
                onChange={(_,value)=>setEdited({...draft,fields:{...draft.fields,[field.id]:(value as string)||''}})}/>
              :field.type==='NumberField'?<Input inputMode='decimal' placeholder={field.hint} style={{width:'100%'}} value={draft.fields[field.id]||''} disabled={submitted||mutate.isPending} onChange={value=>setEdited({...draft,fields:{...draft.fields,[field.id]:value}})}/>
              :<TextArea rows={3} value={draft.fields[field.id]||''} disabled={submitted||mutate.isPending} onChange={value=>setEdited({...draft,fields:{...draft.fields,[field.id]:value}})}/>}
            </Field>)}
        </div>
        {/* 打样申请单上**该由业务员填的格**，一次排齐：两列网格、字段名在上。 */}
        <details open={!query.data?.native_form}>
        <summary>打样明细 Excel 补充资料</summary>
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
          {!query.data?.native_form && (() => { const raw = draft.fields['_estimated_hours'] || ''; const n = Number(raw)
            const points = raw.trim() && Number.isFinite(n) ? n * 10 : null
            return <>
              <Field label="预计工时（小时）" required>
                <Input className="sample-hours-input" style={{width:'100%'}} value={raw} disabled={submitted||mutate.isPending}
                  onChange={value=>{setHoursMissing(false);setEdited({...draft,fields:{...draft.fields,['_estimated_hours']:value}})}}/>
                {/* 这句是"照着做"的提示：告诉人填哪个、系统会替他算什么 */}
                <div style={{marginTop:4,fontSize:12,color:hoursMissing?'var(--crm-error)':'var(--crm-text-3)'}}>
                  {hoursMissing?'请填写预计工时，系统会自动换算打样积分':`按小时填；打样积分由系统按「工时 × 10」自动换算，不用手填`}
                </div>
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
            其余文件（PDF 图纸、Excel…）作为同序 OA 附件随单传上去。 */}
        <div style={{fontWeight:600}}>产品图片（多方位图片）</div>
        {!submitted && <AttachmentPanel businessType="sample" businessId={sampleId} writePermission="sample:manage"
          category="sample_product_image" onlyCategory="sample_product_image" includeUncategorizedImages maxFiles={4}
          accept=".png,.jpg,.jpeg,.gif,.bmp,.webp,.tif,.tiff" uploadLabel="上传产品图片"
          hint="最多4张，仅放入Excel的四个产品图片框，不会作为独立附件传到同序 OA。单个文件不超过20 MB"/>}
        {submitted && <div>已提交的资料已冻结，请在同序 OA 查看本轮附件。</div>}
        </details>
      </>}
      <div style={{fontWeight:600}}>同序 OA 审批过程与结果</div>
      {query.data?.records.map(row=><div key={row.id} style={{border:'1px solid var(--crm-border)',padding:12}}>
        <div>第{row.submit_round??1}轮 · {row.status_label} · 最近同步：{time(row.synced_at)}</div>
        {/* 打开这个弹窗就先向同序 OA 对一次（仍在审批中再每 10 秒对一次）。
            原来只靠 5 秒轮询 CRM 快照 + 手动「立即同步」，OA 批完了这边还挂着"待审"，
            用户以为卡住了。AutoOaSync 自己会判 provider/instance/status，非自建 OA 不触发。 */}
        <AutoOaSync row={row} invalidateKeys={[['sample-dingtalk',sampleId],['samples'],['sample-detail',sampleId]]} />
        {row.instance_id&&<SyncOaButton oaId={row.id} invalidateKeys={[[ 'sample-dingtalk',sampleId ],['samples'],['sample-detail',sampleId]]}/>}
        {row.sync_notice&&<div>{row.sync_notice}</div>}
        <OaFormSnapshot result={row.result}/>
        {row.error&&<div style={{color:'var(--crm-error)'}}>{row.error}</div>}
        <Table dataSource={approvalProgress(row.result).map((r,index)=>({...r,key:index}))} rowKey="key" pagination={false} scroll={{x:700}}
          columns={[{title:'处理人',dataIndex:'person',width:180},{title:'节点/操作',dataIndex:'node',width:150},{title:'状态',dataIndex:'status',width:130},
            {title:'时间',dataIndex:'time',width:180,render:formatApprovalEventTime},{title:'处理意见',dataIndex:'remark'}]}/>
        {/* 「结果待人工核对」的三个出口（认领/重发/作废）。按钮由服务端的
            allowed_actions 决定 —— 打样这条链原先一个都没有，超时后单据彻底卡死。 */}
        <div style={{marginTop:8,display:'flex',gap:12}}>
          <ResolveOaActions row={row} invalidateKeys={[['sample-dingtalk',sampleId],['samples'],['sample-detail',sampleId]]}
            onAdopt={target=>{setAdoptTarget(target);setAdoptKey(newRequestKey())}}
            onDone={()=>void query.refetch()}/>
        </div>
      </div>)}
      {query.data&&!query.data.records.length&&<div>{submitted?'尚未返回可查看的审批记录，请核对原同序 OA 单或稍后刷新。':'尚未提交或关联同序 OA 审批。'}</div>}
      <AdoptOaInstanceModal target={adoptTarget} requestKey={adoptKey}
        invalidateKeys={[['sample-dingtalk',sampleId],['samples'],['sample-detail',sampleId]]}
        onClose={()=>{setAdoptTarget(null);setAdoptKey(null)}}/>
    </div>
  </Modal>
}
