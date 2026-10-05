import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Checkbox, InputNumber, Modal, TextArea, Toast } from '@douyinfe/semi-ui'
import { createOrderDraft, getOrderDraftSource } from '../../shared/api/order'
import { createSampleFromSource, getSampleSource, type SampleSourceRef } from '../../shared/api/sample'

type Draft = { selected: boolean; quantity: number; specification: string; remark: string; unit_price: number | null }
export default function SampleFromSourceModal({ source, onClose, mode = 'sample' }: { source: SampleSourceRef; onClose: () => void; mode?: 'sample' | 'order' }) {
  const navigate = useNavigate()
  const client = useQueryClient()
  const [drafts, setDrafts] = useState<Record<number, Draft>>({})
  const requestKey = useRef(crypto.randomUUID())
  const sending = useRef(false)
  const query = useQuery({ queryKey: ['source-preview', mode, source], queryFn: () => mode === 'order' ? getOrderDraftSource(source) : getSampleSource(source), retry: false, refetchOnWindowFocus: false })
  useEffect(() => {
    if (query.data) setDrafts(Object.fromEntries(query.data.items.map(row => [row.source_item_id, {
      selected: true, quantity: mode === 'sample' ? 1 : (row.original_quantity == null ? NaN : Number(row.original_quantity)), unit_price: row.unit_price == null ? null : Number(row.unit_price), specification: row.specification ?? '', remark: row.remark ?? '',
    }])))
  }, [query.data, mode])
  const mutation = useMutation<{ id: number }, Error, void>({
    mutationFn: () => (mode === 'order' ? createOrderDraft : createSampleFromSource)({ ...source, request_key: requestKey.current,
      items: (query.data?.items ?? []).filter(row => drafts[row.source_item_id]?.selected).map(row => {
        const draft = drafts[row.source_item_id]
        return { source_item_id: row.source_item_id, quantity: draft.quantity,
          ...(mode === 'order' ? { unit_price: draft.unit_price } : {}),
          ...(draft.specification !== (row.specification ?? '') ? { specification: draft.specification } : {}),
          ...(draft.remark !== (row.remark ?? '') ? { remark: draft.remark } : {}),
        }
      }),
    }),
    onSuccess: row => {
      void client.invalidateQueries({ queryKey: [mode === 'order' ? 'order-drafts' : 'samples'] })
      void client.invalidateQueries({ queryKey: ['opportunity-records'] })
      Toast.success(mode === 'order' ? '订单草稿已创建' : '打样申请已创建')
      onClose(); navigate(mode === 'order' ? `/order-drafts/${row.id}` : `/samples/${row.id}`)
    },
    onError: error => Toast.error((error as Error).message),
    onSettled: () => { sending.current = false },
  })
  function change(id: number, patch: Partial<Draft>) {
    if (sending.current) return
    requestKey.current = crypto.randomUUID()
    setDrafts(old => ({ ...old, [id]: { ...old[id], ...patch } }))
  }
  const invalid = !Object.values(drafts).some(row => row.selected) || Object.values(drafts).some(row => row.selected && (!Number.isFinite(row.quantity) || !(row.quantity > 0)))
  return <Modal visible title={mode === 'order' ? '从来源建立订单草稿' : '从来源申请打样'} width={720} confirmLoading={mutation.isPending}
    okText={mode === 'order' ? '建立订单草稿' : '建立打样申请'} okButtonProps={{ disabled: !query.data || invalid }}
    cancelButtonProps={{ disabled: mutation.isPending }} maskClosable={!mutation.isPending}
    onCancel={() => { if (!sending.current) onClose() }} onOk={() => {
      if (!sending.current && !invalid && query.data) { sending.current = true; mutation.mutate() }
    }}>
    {query.isPending && <p>读取来源资料中…</p>}
    {query.error && <p role="alert">{query.error.message}</p>}
    {query.data && <>
      <p>来源：{query.data.source.no} V{query.data.source.version}{query.data.source.is_historical ? '（历史版本）' : ''} · 客户：{query.data.customer_name}</p>
      <p>{mode === 'order' ? '草稿仅供提前准备，不进入生产、不生成应收、不计成交。正式下单前须核对客户确认报价；来源原数量不改。' : '原采购数量保留不变；本次样品数量默认 1 件，可单独修改。草稿与历史版本均可使用。'}</p>
      {query.data.items.map(row => { const draft = drafts[row.source_item_id]; return draft &&
        <div key={row.source_item_id} style={{ borderTop: '1px solid var(--crm-border)', padding: '12px 0' }}>
          <Checkbox checked={draft.selected} disabled={mutation.isPending} onChange={e => change(row.source_item_id, { selected: Boolean(e.target.checked) })}>{row.name}</Checkbox>
          <p>原采购数量：{row.original_quantity == null ? '未记录' : Number(row.original_quantity).toLocaleString('zh-CN')}</p>
          <div>{mode === 'order' ? '本次下单数量：' : '本次样品数量：'}<InputNumber min={0.001} precision={3} value={Number.isFinite(draft.quantity) ? draft.quantity : undefined} disabled={mutation.isPending || !draft.selected}
            onChange={v => change(row.source_item_id, { quantity: Number(v) })} /></div>
          {mode === 'order' && <div style={{ marginTop: 8 }}>草稿单价：<InputNumber min={0} precision={4} value={draft.unit_price ?? undefined} placeholder="未核价" disabled={mutation.isPending || !draft.selected} onChange={v => change(row.source_item_id, { unit_price: v === '' || v == null ? null : Number(v) })} /></div>}
          <p>本次规格</p><TextArea value={draft.specification} disabled={mutation.isPending || !draft.selected} onChange={v => change(row.source_item_id, { specification: v })} />
          {draft.specification !== (row.specification ?? '') && <p>原规格：{row.specification || '未记录'}</p>}
          <p>本次备注</p><TextArea value={draft.remark} disabled={mutation.isPending || !draft.selected} onChange={v => change(row.source_item_id, { remark: v })} />
        </div>
      })}
    </>}
  </Modal>
}
