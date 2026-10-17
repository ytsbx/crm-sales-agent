import { formatDingTalkTime } from '../../shared/beijing-time'
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Modal, Toast } from '@douyinfe/semi-ui'
import { api } from '../../shared/api/client'
import type { ContractDocument } from '../../shared/api/contract'
import { autoLinkContractDingTalkApproval, startContractDingTalkApproval } from '../../shared/api/contract'
import { downloadContractDoc } from '../../shared/download-contract'

type Entry = { mode: string; url: string | null; doc_no: string; title: string }
export default function CustomerApproval({ doc, onClose }: { doc: ContractDocument | null; onClose: () => void }) {
  const [instanceId, setInstanceId] = useState('')
  const cache = useQueryClient()
  const entry = useQuery({ queryKey: ['contract-approval-entry', doc?.id], queryFn: () => api.get<Entry>(`/contract-documents/${doc!.id}/approval-entry`), enabled: Boolean(doc), retry: false })
  const autoLink = useMutation({
    mutationFn: () => autoLinkContractDingTalkApproval(doc!.id),
    onSuccess: () => {
      Toast.success('已自动找到并关联钉钉审批')
      void cache.invalidateQueries({ queryKey: ['contract-documents'] })
      void cache.invalidateQueries({ queryKey: ['contract-detail', doc?.id] })
      onClose()
    },
    onError: (e: Error) => { if (!/暂未找到匹配/.test(e.message)) Toast.error(e.message) },
  })
  useEffect(() => {
    if (!doc || entry.data?.mode !== 'external_link' || autoLink.isPending) return
    const timer = window.setInterval(() => autoLink.mutate(), 10000)
    return () => window.clearInterval(timer)
  }, [doc, entry.data?.mode, autoLink.isPending])

  const submit = useMutation({
    mutationFn: async () => {
      if (entry.data?.mode === 'api') return startContractDingTalkApproval(doc!.id)
      return api.post(`/contract-documents/${doc!.id}/approval-link`, { instance_id: instanceId.trim() })
    },
    onSuccess: (result) => {
      if (entry.data?.mode === 'api') {
        const outcome = (result as { result?: { level?: string; message?: string } }).result
        if (outcome?.level !== 'success') { Toast.warning(outcome?.message || '请核对审批状态'); return }
      }
      Toast.success(entry.data?.mode === 'api' ? '已提交钉钉审批' : '已核验并关联审批')
      void cache.invalidateQueries({ queryKey: ['contract-documents'] })
      void cache.invalidateQueries({ queryKey: ['contract-detail', doc?.id] })
      setInstanceId('')
      onClose()
    },
    onError: (e: Error) => Toast.error(e.message),
  })
  return <Modal title="钉钉合同审批" visible={Boolean(doc)} onCancel={onClose}
    width={520} style={{ maxWidth: 'calc(100vw - 32px)' }}
    bodyStyle={{ paddingTop: 8, paddingBottom: 24 }}
    footer={entry.data?.mode === 'api' ? <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 12 }}>
      <Button disabled={submit.isPending} onClick={onClose}>取消</Button>
      <Button theme="solid" loading={submit.isPending} onClick={() => submit.mutate()}>提交钉钉审批</Button>
    </div> : null}>
    {entry.isLoading && <p>正在核对审批入口…</p>}
    {entry.error && <p role="alert">{entry.error.message}</p>}
    {entry.data?.mode === 'external_link' && <div style={{ display: 'grid', gap: 12 }}>
      <p>请在钉钉上传本合同的完整原文件，填写后提交。打开页面不代表已提交审批。</p>
      <div>合同来源：{doc?.contract_origin_label || '历史未标注'}</div>
      <div>合同名称：{doc?.title}</div>
      <div>合同编号：{doc?.doc_no}（钉钉中选择“手动填写”，按此编号填写）</div>
      <div style={{ display: 'flex', gap: 12 }}>
        <Button onClick={() => void downloadContractDoc(doc!)}>下载合同原文件</Button>
        <a href={entry.data.url!} target="_blank" rel="noreferrer">打开钉钉合同审批</a>
      </div>
      <p>提交后，将钉钉审批详情链接（含审批编号）或审批实例编号粘贴到下方。系统核对流程、本人账号、合同名称和编号后关联；请确认钉钉正文附件为本合同原文件。</p>
      <Button theme="solid" loading={autoLink.isPending} onClick={() => autoLink.mutate()}>立即查找并关联</Button>
      <details><summary>自动查找不到时，手动关联</summary>
      <Input value={instanceId} onChange={setInstanceId} placeholder="粘贴钉钉审批详情链接或审批实例编号" />
      <Button theme="solid" loading={submit.isPending} onClick={() => {
        if (!instanceId.trim()) { Toast.warning('请粘贴审批详情链接或实例编号'); return }
        submit.mutate()
      }}>手动核验并关联</Button></details>
    </div>}
    {entry.data?.mode === 'api' && <div style={{ display: 'grid', gap: 16 }}>
      <div style={{ padding: '16px 20px', background: 'var(--semi-color-fill-0)', borderRadius: 8, display: 'grid', gap: 8 }}>
        <div style={{ fontSize: 15, fontWeight: 600, overflowWrap: 'anywhere' }}>{doc?.title}</div>
        <div style={{ color: 'var(--semi-color-text-2)', fontSize: 13 }}>合同编号：{doc?.doc_no}</div>
      </div>
      <div style={{ lineHeight: 1.7, color: 'var(--semi-color-text-1)' }}>将上传合同原文件并提交到钉钉，审批操作在钉钉完成。</div>
    </div>}
  </Modal>
}

const LABELS: Record<string, string> = { RUNNING: '办理中', NEW: '待处理', COMPLETED: '已完成', TERMINATED: '已撤回', agree: '同意', refuse: '驳回', NONE: '未处理', AGREE: '同意', REFUSE: '驳回', FINISH: '已完成', FINISHED: '已完成', EXECUTE_TASK_NORMAL: '审批处理', EXECUTE_TASK_AGENT: '代理审批', START_PROCESS_INSTANCE: '发起审批', TERMINATE_PROCESS_INSTANCE: '撤回审批', ADD_REMARK: '评论', REDIRECT_PROCESS: '转交', COMMENT: '评论', START: '发起', PENDING: '待处理', CANCELLED: '已取消' }
function label(value: unknown) { return typeof value === 'string' ? LABELS[value] || '已记录' : '' }
export function ApprovalHistory({ result }: { result: Record<string, unknown> }) {
  const payload = (result.payload || {}) as Record<string, unknown>
  const tasks = Array.isArray(payload.tasks) ? payload.tasks as Record<string, unknown>[] : []
  const records = Array.isArray(payload.operationRecords) ? payload.operationRecords as Record<string, unknown>[] : []
  const time = (value: unknown) => typeof value === 'string' && !Number.isNaN(Date.parse(value)) ? formatDingTalkTime(value) : ''
  const person = (row: Record<string, unknown>) => String(row.userName || row.staffName || '钉钉未返回处理人姓名')
  return <div style={{ display: 'grid', gap: 8, marginTop: 8 }}>
    <b>审批节点</b>
    {tasks.length ? tasks.map((row, i) => <div key={i}>{person(row)}{row.activityName ? ` · ${String(row.activityName)}` : ''} · {label(row.taskStatus || row.status)} {label(row.taskResult || row.result)} · {time(row.finishTime || row.endTime || row.createTime)}</div>) : <div>钉钉尚未返回节点明细</div>}
    <b>处理记录</b>
    {records.length ? records.map((row, i) => <div key={i}>{person(row)}{row.showName ? ` · ${String(row.showName)}` : ''} · {label(row.operationType || row.type || row.result)} · {time(row.date || row.createTime)}{row.remark ? ` · ${String(row.remark)}` : ''}</div>) : <div>暂无处理记录</div>}
  </div>
}

export function ContractBasis({ doc }: { doc: ContractDocument }) {
  const [choice, setChoice] = useState('')
  const cache = useQueryClient()
  const choices = useQuery({ queryKey: ['contract-basis', doc.customer_id], queryFn: async () => {
    const [orders, quotes] = await Promise.all([
      api.get<{ items: { id: number; order_no: string }[] }>('/orders', { customer_id: doc.customer_id, page_size: 200 }),
      api.get<{ items: { id: number; quote_no: string }[] }>('/quotes', { customer_id: doc.customer_id, page_size: 200 }),
    ])
    return [...orders.items.map((v) => ({ key: `order:${v.id}`, text: `订单 ${v.order_no}` })), ...quotes.items.map((v) => ({ key: `quote:${v.id}`, text: `报价 ${v.quote_no}` }))]
  } })
  const save = useMutation({ mutationFn: () => {
    const [kind, id] = choice.split(':')
    return api.post(`/contract-documents/${doc.id}/basis`, { [kind === 'order' ? 'order_id' : 'quote_id']: Number(id) })
  }, onSuccess: () => { Toast.success('业务依据已保存'); void cache.invalidateQueries({ queryKey: ['contract-detail', doc.id] }); void cache.invalidateQueries({ queryKey: ['contract-documents'] }) }, onError: (e: Error) => Toast.error(e.message) })
  return <div style={{ display: 'grid', gap: 8 }}>
    <div>归档前请补充正式订单或客户已收到的有效报价：</div>
    <select aria-label="合同业务依据" value={choice} onChange={(e) => setChoice(e.target.value)}>
      <option value="">选择订单或报价</option>
      {(choices.data || []).map((v) => <option key={v.key} value={v.key}>{v.text}</option>)}
    </select>
    <Button loading={save.isPending} onClick={() => { if (!choice) Toast.warning('请选择业务依据'); else save.mutate() }}>保存业务依据</Button>
    {choices.error && <span role="alert">{choices.error.message}</span>}
  </div>
}
