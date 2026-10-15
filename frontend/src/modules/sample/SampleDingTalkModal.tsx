import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, DatePicker, Input, Modal, Select, Table, TextArea, Toast } from '@douyinfe/semi-ui'
import { api } from '../../shared/api/client'
import { type OaApprovalInstance } from '../../shared/api/dingtalk'
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
  return <Modal visible title="打样钉钉流程" width={900} bodyStyle={{maxHeight:'70vh',overflowY:'auto'}}
    onCancel={()=>{if(!mutate.isPending)onClose()}}
    footer={<div style={{display:'flex',justifyContent:'flex-end',gap:10}}>
      <Button disabled={mutate.isPending} onClick={onClose}>关闭</Button>
      {submitted && query.data?.records[0]?.status==='rejected' && <Button disabled={mutate.isPending} onClick={()=>setEdited({...draft,resubmit:true})}>修改后重新提交</Button>}
      {!submitted&&<Button loading={mutate.isPending} disabled={!query.data} onClick={()=>mutate.mutate(false)}>保存草稿</Button>}
      {(!submitted||query.data?.records[0]?.allowed_actions?.includes('retry'))&&<Button theme="solid" loading={mutate.isPending} disabled={!query.data} onClick={()=>mutate.mutate(true)}>
        {submitted?'重试原申请':draft.mode==='inquiry'?'关联原询价（不发新单）':'提交打样到钉钉'}
      </Button>}
    </div>}>
    {query.isError&&<div style={{color:'var(--crm-error)'}}>{query.error.message}</div>}
    <div style={{display:'grid',gridTemplateColumns:'minmax(0,1fr)',gap:12}}>
      <div>生产部在钉钉处理，CRM每约3分钟同步结果和过程。保存草稿不会发出审批。</div>
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
        <div style={{fontWeight:600}}>随审批上传的申请表内容（可先保存草稿，提交前请核对）</div>
        {([['product_name','产品名称'],['material_spec','主要材质和规格'],['product_size','产品尺寸'],['printing','产品印刷'],['accessories','主要辅料'],['packaging','包装要求'],['craft_requirement','做法要求']] as const).map(([key,label])=><div key={key}>
          <div>{label}</div><TextArea rows={2} value={draft.fields['_'+key]||''} disabled={submitted||mutate.isPending}
            onChange={value=>setEdited({...draft,fields:{...draft.fields,['_'+key]:value}})}/>
        </div>)}
        <div>图纸、照片和其他制作资料（保存到当前打样单）</div>
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
      </div>)}
      {query.data&&!query.data.records.length&&<div>{submitted?'尚未返回可查看的审批记录，请核对原钉钉单或稍后刷新。':'尚未提交或关联钉钉审批。'}</div>}
    </div>
  </Modal>
}
