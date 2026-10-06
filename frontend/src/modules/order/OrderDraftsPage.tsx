import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, DatePicker, Input, InputNumber, Popconfirm, Select, Table, TextArea, Toast } from '@douyinfe/semi-ui'
import PageHeader from '../../shared/components/PageHeader'
import SectionCard from '../../shared/components/SectionCard'
import { usePermissions } from '../../shared/hooks/permissions'
import { confirmOrderDraft, generateOrderDraftDocument, getOrderDraft, listOrderDrafts, updateOrderDraft, type OrderDraft, type OrderDraftLine } from '../../shared/api/order'
import { listQuotes } from '../../shared/api/quote'
import { downloadBizDoc, listBizDocs } from '../../shared/api/bizdoc'
import { newRequestKey } from '../../shared/api/requestKey'

export default function OrderDraftsPage() {
  const { id } = useParams()
  const draftId = Number(id)
  const navigate = useNavigate()
  const client = useQueryClient()
  const { can } = usePermissions()
  const query = useQuery({ queryKey: ['order-draft', draftId], queryFn: () => getOrderDraft(draftId), enabled: !!id, refetchOnWindowFocus: false, refetchOnReconnect: false })
  const [page, setPage] = useState(1)
  const list = useQuery({ queryKey: ['order-drafts', page], queryFn: () => listOrderDrafts({ page }), enabled: !id })
  const [rows, setRows] = useState<OrderDraftLine[]>([])
  const [form, setForm] = useState({ currency: 'CNY', delivery_date: '', payment_terms: '', remark: '' })
  const [versionId, setVersionId] = useState<number | undefined>()
  const [dirty, setDirty] = useState(false)
  useEffect(() => { if (query.data) {
    setRows(query.data.items.map(i => ({ ...i })))
    setForm({ currency: query.data.currency, delivery_date: query.data.delivery_date ?? '', payment_terms: query.data.payment_terms ?? '', remark: query.data.remark ?? '' })
    setDirty(false)
  } }, [query.data])
  const quotes = useQuery({ queryKey: ['draft-confirmed-quotes', query.data?.customer_id, query.data?.opportunity_id],
    queryFn: async () => {
      const filter = { status: 'accepted', customer_id: query.data!.customer_id, opportunity_id: query.data!.opportunity_id ?? undefined, page_size: 100 }
      const first = await listQuotes({ ...filter, page: 1 })
      const all = [...first.items]
      for (let page = 2; all.length < first.total; page += 1) { const next = await listQuotes({ ...filter, page }); if (!next.items.length) break; all.push(...next.items) }
      return { ...first, items: all }
    },
    enabled: !!query.data && can('quote:view') })
  const docs = useQuery({ queryKey: ['draft-documents', draftId], queryFn: () => listBizDocs({ order_draft_id: draftId }), enabled: !!id })
  const error = (e: Error) => Toast.error(e.message)
  const save = useMutation({ mutationFn: () => updateOrderDraft(draftId, { revision: query.data!.revision,
    ...form, delivery_date: form.delivery_date || null, items: rows.map(i => ({ source_item_id: i.source_item_id, quantity: i.quantity,
      unit_price: i.unit_price, specification: i.specification, remark: i.remark })) }),
    onSuccess: data => { client.setQueryData(['order-draft', draftId], data); Toast.success('草稿已保存') }, onError: error })
  const confirm = useMutation({ mutationFn: () => confirmOrderDraft(draftId, query.data!.revision, versionId!),
    onSuccess: data => { void client.invalidateQueries({ queryKey: ['orders'] }); void client.invalidateQueries({ queryKey: ['order-drafts'] }); navigate(`/orders/${data.order_id}`); Toast.success('已正式下单') }, onError: error })
  // 幂等键（§8.9）：同一把键在成功之前保持不变，弱网重试才不会多出一份；
  // 成功即清空，下一次点击必然是**新键**（= 明确再出一版，不按内容去重）。
  const docRequestKey = useRef<string | null>(null)
  const generate = useMutation({ mutationFn: () => {
      if (!docRequestKey.current) docRequestKey.current = newRequestKey()
      return generateOrderDraftDocument(draftId, docRequestKey.current)
    },
    onSuccess: () => { docRequestKey.current = null; void docs.refetch(); Toast.success('草稿需求单已生成') }, onError: error })
  const busy = save.isPending || confirm.isPending || generate.isPending
  function patch(index: number, change: Partial<OrderDraftLine>) { if (Object.entries(change).every(([key, value]) => rows[index]?.[key as keyof OrderDraftLine] === value)) return; setDirty(true); setRows(old => old.map((row, i) => i === index ? { ...row, ...change } : row)) }
  if (!id) return <div className="page-container"><PageHeader title="订单草稿" subtitle="提前准备资料，不进入生产、应收或成交统计" extra={<Link to="/orders">返回订单中心</Link>} />
    {list.error && <p>{list.error.message}</p>}
    <SectionCard><Table<OrderDraft> dataSource={list.data?.items ?? []} loading={list.isPending} rowKey="id" pagination={{ currentPage: page, pageSize: 20, total: list.data?.total ?? 0, onPageChange: setPage }} columns={[
      { title: '草稿', render: (_: unknown, row: OrderDraft) => <Link to={`/order-drafts/${row.id}`}>草稿 #{row.id}</Link> },
      { title: '来源', render: (_: unknown, row: OrderDraft) => `${row.source_context.no} V${row.source_context.version}` },
      { title: '状态', render: (_: unknown, row: OrderDraft) => row.status === 'converted' ? '已转正式订单' : '准备中' },
      { title: '明细数', render: (_: unknown, row: OrderDraft) => row.items.length },
    ]} /></SectionCard></div>
  if (!query.data) return <div className="page-container">{query.error?.message ?? '读取草稿中…'}</div>
  const draft = query.data
  const editable = draft.status === 'draft' && can('order:manage') && !busy
  return <div className="page-container"><PageHeader title={`订单草稿 #${draft.id}`} subtitle="草稿不代表客户已下单，不进入生产、不生成应收、不计成交" extra={<Link to="/order-drafts">全部草稿</Link>} />
    <SectionCard title="来源与本次明细"><p>来源：{draft.source_context.no} V{draft.source_context.version} · 草稿修订 {draft.revision} · 币种 {draft.currency}</p>
      {rows.map((row, index) => <div key={row.id} style={{ borderTop: '1px solid var(--crm-border)', padding: '12px 0' }}>
        <b>{row.name}</b><p>原采购数量：{row.source_snapshot.original_quantity == null ? '未记录' : Number(row.source_snapshot.original_quantity).toLocaleString('zh-CN')}</p>
        <div style={{ display: 'flex', gap: 16 }}>本次数量：<InputNumber min={0.001} precision={3} value={row.quantity} disabled={!editable} onChange={v => patch(index, { quantity: Number(v) })} />
          单价：<InputNumber min={0} precision={4} value={row.unit_price ?? undefined} placeholder="未核价" disabled={!editable} onChange={v => patch(index, { unit_price: v === '' || v == null ? null : Number(v) })} /></div>
        <p>本次规格</p><TextArea value={row.specification ?? ''} disabled={!editable} onChange={v => patch(index, { specification: v })} />
        {(row.specification ?? '') !== (row.source_snapshot.specification ?? '') && <p>原规格：{row.source_snapshot.specification || '未记录'}</p>}
        <p>本次备注</p><TextArea value={row.remark ?? ''} disabled={!editable} onChange={v => patch(index, { remark: v })} />
      </div>)}
      <p>币种</p><Input value={form.currency} disabled={!editable} onChange={v => { setDirty(true); setForm({ ...form, currency: v.toUpperCase() }) }} />
      <p>计划交期</p><DatePicker type="date" format="yyyy-MM-dd" showClear style={{ width: '100%' }} placeholder="选择计划交期" value={form.delivery_date ? new Date(form.delivery_date) : undefined} disabled={!editable} onChange={(_, v) => { setDirty(true); setForm({ ...form, delivery_date: (v as string) || '' }) }} />
      <p>付款条件</p><Input value={form.payment_terms} disabled={!editable} onChange={v => { setDirty(true); setForm({ ...form, payment_terms: v }) }} />
      <p>备注</p><TextArea value={form.remark} disabled={!editable} onChange={v => { setDirty(true); setForm({ ...form, remark: v }) }} />
      {draft.status === 'draft' && can('order:manage') && <Button style={{ marginTop: 12 }} loading={save.isPending} disabled={!editable || !dirty} onClick={() => save.mutate()}>保存草稿</Button>}
    </SectionCard>
    <SectionCard title="草稿需求单" style={{ marginTop: 16 }}><p>生成文件会保存当时的草稿和差异；以后修改不会覆盖旧文件。</p>
      {can('order:manage') && <Button disabled={busy || dirty} loading={generate.isPending} onClick={() => generate.mutate()}>生成草稿需求单</Button>}
      {(docs.data ?? []).map(doc => <p key={doc.id}>{doc.doc_no} V{doc.version} · <a onClick={() => downloadBizDoc(doc)}>下载 PDF</a></p>)}
    </SectionCard>
    <SectionCard title="确认正式下单" style={{ marginTop: 16 }}>
      {draft.order_id ? <Link to={`/orders/${draft.order_id}`}>查看正式订单 #{draft.order_id}</Link> : <>
        <p>请选择同一客户、同一商机需求中客户已接受的报价。草稿须与该版本的全部明细、价格和付款条件一致；有改动请先修订报价并取得客户确认。</p>
        <Select placeholder="选择客户已确认报价" style={{ width: 360 }} value={versionId} disabled={busy || !can('order:manage')} optionList={(quotes.data?.items ?? []).filter(q => q.current_version_id).map(q => ({ value: q.current_version_id!, label: q.quote_no }))} onChange={v => setVersionId(Number(v))} />
        {/* 正式下单会真的建订单、进入履约，且不能一键撤回，必须二次确认
            （主人 2026-10-06 定的范围里明确包含「正式下单」）。 */}
        <Popconfirm
          title="确认正式下单？"
          content="确认后会按这份草稿生成正式订单并进入履约流程，请再核对一遍明细与付款条件。"
          onConfirm={() => confirm.mutate()}
        >
          <Button theme="solid" style={{ marginLeft: 12 }} disabled={busy || dirty || !versionId || !can('order:manage')} loading={confirm.isPending}>确认正式下单</Button>
        </Popconfirm>
        {quotes.error && <p>{quotes.error.message}</p>}
        {versionId && <p><Link to={`/quotes/${quotes.data?.items.find(q => q.current_version_id === versionId)?.id}?version=${versionId}`}>查看选定报价明细</Link></p>}
        {dirty && <p>请先保存草稿，再核对并下单。</p>}
      </>}
    </SectionCard>
  </div>
}
