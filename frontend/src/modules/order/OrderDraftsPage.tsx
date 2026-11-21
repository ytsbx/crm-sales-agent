import { showDownloadResult } from '../../shared/download-feedback'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Banner, Button, DatePicker, Input, Popconfirm, Select, Table, Tag, TextArea, Toast } from '@douyinfe/semi-ui'
import PageHeader from '../../shared/components/PageHeader'
import SectionCard from '../../shared/components/SectionCard'
import BlockedHint from '../../shared/components/BlockedHint'
import GuardedButton from '../../shared/components/GuardedButton'
import FormLabel from '../../shared/components/FormLabel'
import { formatMoney } from '../../shared/components/money'
import { usePermissions } from '../../shared/hooks/permissions'
import { useTabLabel } from '../../shared/hooks/useTabLabel'
import { confirmOrderDraft, generateOrderDraftDocument, getOrderDraft, listOrderDrafts, updateOrderDraft, type OrderDraft, type OrderDraftLine } from '../../shared/api/order'
import { listQuotes } from '../../shared/api/quote'
import { getCustomer } from '../../shared/api/customer'
import { downloadBizDoc, listBizDocs } from '../../shared/api/bizdoc'
import { formatQuantityText } from '../../shared/number-input'
import { newRequestKey } from '../../shared/api/requestKey'

export default function OrderDraftsPage() {
  const { id } = useParams()
  const draftId = Number(id)
  const navigate = useNavigate()
  const client = useQueryClient()
  const { can } = usePermissions()
  const query = useQuery({ queryKey: ['order-draft', draftId], queryFn: () => getOrderDraft(draftId), enabled: !!id, refetchOnWindowFocus: false, refetchOnReconnect: false })
  /* 草稿本身只有 customer_id，没有客户名 —— 详情页标题只写「订单草稿」时，
     打开三个草稿页签根本分不清哪个是哪个客户（2026-11-20 截图）。
     单独补一次客户读取，只为了在标题里写出客户名。 */
  const customerQuery = useQuery({
    queryKey: ['customer', query.data?.customer_id],
    queryFn: () => getCustomer(query.data!.customer_id),
    enabled: !!query.data?.customer_id,
    staleTime: 60_000,
  })
  // 页签标题带草稿号，避免多个「订单草稿详情」分不清（编号写法照 2026-10-12 口径：不用 #）
  useTabLabel(id && query.data ? `草稿编号 ${draftId}` : null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const list = useQuery({ queryKey: ['order-drafts', page, pageSize], queryFn: () => listOrderDrafts({ page, page_size: pageSize }), enabled: !id })
  const [rows, setRows] = useState<OrderDraftLine[]>([])
  const [form, setForm] = useState({ currency: 'CNY', delivery_date: '', payment_terms: '', remark: '' })
  const [quoteSelection, setQuoteSelection] = useState<{ draftId: number; versionId: number }>()
  const [dirty, setDirty] = useState(false)
  /* dirty 的镜像：setState 是异步的，effect 里读 state 可能读到旧值，
     所以再存一份 ref，让「保存回来时用户手上有没有没保存的改动」能当场判断。 */
  const dirtyRef = useRef(false)
  /* 已经灌过哪一份草稿。保存成功回来的那份不再灌（见下面的 effect）。 */
  const hydratedRef = useRef<number | null>(null)
  /* 数量/单价是 decimal 原值，后端给的是 "3200.000"、"22.0000" 这种带尾零的字符串，
     直接塞进输入框，满屏都是没意义的小数位（2026-11-20 截图）。
     只清掉无意义的尾零，数值本身不变。 */
  const trimZeros = (v: string | number | null | undefined) => {
    if (v == null || v === '') return ''
    const s = String(v)
    return s.includes('.') ? s.replace(/\.0+$/, '').replace(/(\.\d*[1-9])0+$/, '$1') : s
  }
  const markDirty = (value: boolean) => {
    dirtyRef.current = value
    setDirty(value)
  }
  /* 这份 effect 只负责「把服务器数据灌进本地一次」：进来一份新草稿（id 变了）才灌。
     保存成功后 save 会把服务器返回的整份草稿 setQueryData 回来，query.data 会变成
     一个新对象、同样会触发这里 —— 如果每次都照灌，就等于拿服务器那份把桌面全换掉，
     用户正在改、还没保存的行和表头会被一起冲掉（还会把 dirty 清成 false，
     让「草稿有未保存改动，请先保存再下单」这道拦截失效）。
     所以用 hydratedRef 记住「这份草稿已经灌过了」，之后 query.data 再怎么变（都是保存
     回来的服务器版），都不再整份覆盖本地。 */
  useEffect(() => {
    if (!query.data) return
    if (hydratedRef.current === query.data.id) return
    hydratedRef.current = query.data.id
    setRows(query.data.items.map(i => ({ ...i, quantity: trimZeros(i.quantity_exact ?? i.quantity), unit_price: i.unit_price_exact == null && i.unit_price == null ? null : trimZeros(i.unit_price_exact ?? i.unit_price) })))
    setForm({ currency: query.data.currency, delivery_date: query.data.delivery_date ?? '', payment_terms: query.data.payment_terms ?? '', remark: query.data.remark ?? '' })
    markDirty(false)
  }, [query.data])
  const quotes = useQuery({ queryKey: ['draft-confirmed-quotes', query.data?.customer_id, query.data?.opportunity_id],
    queryFn: async () => {
      const filter = { status: 'accepted', customer_id: query.data!.customer_id, opportunity_id: query.data!.opportunity_id ?? undefined, page_size: 100 }
      const first = await listQuotes({ ...filter, page: 1 })
      const all = [...first.items]
      for (let page = 2; all.length < first.total; page += 1) { const next = await listQuotes({ ...filter, page }); if (!next.items.length) break; all.push(...next.items) }
      return { ...first, items: all }
    },
    enabled: query.data?.status === 'draft' && can('quote:view') })
  const quoteOptions = (quotes.data?.items ?? []).filter(quote => quote.current_version_id != null)
  const selectedQuote = quoteSelection?.draftId === draftId
    ? quoteOptions.find(quote => quote.current_version_id === quoteSelection.versionId)
    : undefined
  const docs = useQuery({ queryKey: ['draft-documents', draftId], queryFn: () => listBizDocs({ order_draft_id: draftId }), enabled: !!id })
  const error = (e: Error) => Toast.error(e.message)
  const save = useMutation({ mutationFn: () => updateOrderDraft(draftId, { revision: query.data!.revision,
    ...form, delivery_date: form.delivery_date || null, items: rows.map(i => ({ source_item_id: i.source_item_id, quantity: i.quantity,
      unit_price: i.unit_price, specification: i.specification, remark: i.remark })) }),
    onSuccess: data => {
      client.setQueryData(['order-draft', draftId], data)
      /* 这一下确实把点按钮那一刻的改动存下了，脏标记该清；
         但本地 rows/form 保持原样，不跟着服务器版整份重画。 */
      markDirty(false)
      Toast.success('草稿已保存')
    }, onError: error })
  const confirm = useMutation({
    mutationFn: ({ id, revision, versionId }: { id: number; revision: number; versionId: number }) =>
      confirmOrderDraft(id, revision, versionId),
    onSuccess: data => {
      void client.invalidateQueries({ queryKey: ['orders'] })
      void client.invalidateQueries({ queryKey: ['order-drafts'] })
      void client.invalidateQueries({ queryKey: ['order-draft', draftId] })
      navigate(`/orders/${data.order_id}`)
      Toast.success(`正式订单 ${data.order_no} 已建立`)
    },
  })
  // 幂等键（§8.9）：同一把键在成功之前保持不变，弱网重试才不会多出一份；
  // 成功即清空，下一次点击必然是**新键**（= 明确再出一版，不按内容去重）。
  const docRequestKey = useRef<string | null>(null)
  const generate = useMutation({ mutationFn: () => {
      if (!docRequestKey.current) docRequestKey.current = newRequestKey()
      return generateOrderDraftDocument(draftId, docRequestKey.current)
    },
    onSuccess: () => { docRequestKey.current = null; void docs.refetch(); Toast.success('草稿需求单已生成') }, onError: error })
  const busy = save.isPending || confirm.isPending || generate.isPending
  function patch(index: number, change: Partial<OrderDraftLine>) { if (Object.entries(change).every(([key, value]) => rows[index]?.[key as keyof OrderDraftLine] === value)) return; markDirty(true); setRows(old => old.map((row, i) => i === index ? { ...row, ...change } : row)) }
  if (!id) return <div className="page-container"><PageHeader title="订单草稿" subtitle="提前准备资料，不进入生产、应收或成交统计" extra={<Link to="/orders">返回订单中心</Link>} />
    {list.error && <p>{list.error.message}</p>}
    {/* auto-height：单卡列表页会被 .page-container 的 flex 规则拉满整屏，
        草稿只有一条时卡片下半截全是白，像没加载完。内容少就按内容高度收住。
        注意注释必须放在花括号里，否则会被当成正文渲染到页面上。 */}
    <SectionCard className="auto-height"><Table<OrderDraft> dataSource={list.data?.items ?? []} loading={list.isPending} rowKey="id" /* 一页装得下就不画分页条（2026-11-21 巡检）：草稿往往只有一两条，
   原来照样挂「显示第 1 条-第 1 条，共 1 条 + 翻页 + 每页条数：20」，
   翻页键和每页条数都是死的，白占一行。数据多过一页时原样出现。 */
          pagination={(list.data?.total ?? 0) <= pageSize ? false : { currentPage: page, pageSize, total: list.data?.total ?? 0, onPageChange: setPage, showSizeChanger: true, pageSizeOpts: [10, 20, 50, 100], onPageSizeChange: (size: number) => { setPageSize(size); setPage(1) } }} columns={[
      /* 2026-11-20：四列原来都不写 width，Semi 把 1170 宽平分成四格，
         一行数据被摊在四个 290 宽的格子里、彼此隔着大半屏，像没排版。
         现在按内容给宽度：草稿 / 状态 / 明细数窄，来源（报价号 + 版本）最宽，
         操作列放"继续填写"，最末一列不设宽吃掉剩余。 */
      { title: '草稿', width: 150, render: (_: unknown, row: OrderDraft) => <Link to={`/order-drafts/${row.id}`}>草稿 #{row.id}</Link> },
      /* 来源字段可能缺（手工建的草稿、或历史草稿没挂报价版本）：
         直接拼模板会渲染成「undefined Vundefined」，这里缺字段就写人话。 */
      { title: '来源报价', width: 280, render: (_: unknown, row: OrderDraft) => {
        const no = row.source_context?.no
        if (!no) return <span style={{ color: 'var(--crm-text-3)' }}>未挂报价（手工建）</span>
        const version = row.source_context?.version
        return version ? `${no} V${version}` : no
      } },
      {
        title: '状态',
        width: 170,
        render: (_: unknown, row: OrderDraft) => row.status === 'converted'
          ? <Tag color="green" size="small">已转正式订单</Tag>
          : <Tag color="orange" size="small">准备中</Tag>,
      },
      { title: '明细数', width: 130, render: (_: unknown, row: OrderDraft) => `${row.items.length} 项` },
      {
        title: '操作',
        render: (_: unknown, row: OrderDraft) => (
          <Link to={`/order-drafts/${row.id}`}>{row.status === 'converted' ? '查看' : '继续填写'}</Link>
        ),
      },
    ]} />
      {/* 分页条被收掉时总数就没人说了（一屏最多装 pageSize 条），
          右下角补一句「共 N 条」把口径补回来。 */}
      {((list.data?.total ?? 0) <= pageSize) && (
        <div style={{ marginTop: 10, textAlign: 'right', fontSize: 12, color: 'var(--crm-text-3)' }}>
          共 {list.data?.total ?? 0} 条
        </div>
      )}
    </SectionCard></div>
  if (!query.data) return <div className="page-container">{query.error?.message ?? '读取草稿中…'}</div>
  const draft = query.data
  const editable = draft.status === 'draft' && can('order:manage') && !busy
  const quoteAmount = selectedQuote?.current_version_amount_exact ?? selectedQuote?.current_version_amount
  const confirmationError = confirm.variables?.id === draftId ? confirm.error?.message : undefined
  const draftMismatch = confirmationError?.startsWith('草稿明细、币种或付款条件与客户确认报价不一致')
  const confirmBlocked = !can('order:manage')
    ? '需要订单管理权限才能正式下单。'
    : !can('quote:view')
      ? '需要报价查看权限才能选择下单依据。'
      : quotes.isError
        ? '请先重新读取报价。'
        : quotes.isPending
          ? '正在读取报价。'
          : quoteOptions.length === 0
            ? '还没有可用的已接受报价。请先在报价中心登记客户接受，再回来下单。'
            : dirty
              ? '请先在上方保存草稿。'
              : !selectedQuote
                ? '请先选择客户已确认的报价。'
                : busy && !confirm.isPending
                  ? '请等待当前操作完成。'
                  : ''
  return <div className="page-container"><PageHeader
    title={`草稿 ${draftId} · ${customerQuery.data?.name ?? '客户读取中…'}`}
    subtitle={`来源 ${draft.source_context.no ?? '未挂报价'}${draft.source_context.version ? ` V${draft.source_context.version}` : ''} · 第 ${draft.revision} 版 · 币种 ${draft.currency} · 草稿不代表客户已下单`}
    extra={<Link to="/order-drafts">全部草稿</Link>}
  />
    <SectionCard id="order-draft-details" title="来源与本次明细">
      {rows.map((row, index) => <div key={row.id} style={{ borderTop: '1px solid var(--crm-border)', padding: '12px 0' }}>
        {/* 同一份报价里「中空板箱01-1」常按规格拆成两行，两条都写同名，
            上下两段看着像重复录入。加序号区分第几项。 */}
        <b>第 {index + 1} 项 · {row.name}</b>
        {row.source_snapshot.original_quantity != null && <p>原报价数量：{formatQuantityText(row.source_snapshot.original_quantity)}</p>}
        {/* 「本次数量：」「单价：」原来跟输入框平铺在一个 flex 行里：窄屏放不下时，
            标签会被压成「本/次/数/量」逐字换行（461px 实拍）。现在每组包一层
            nowrap 小盒子，整组往下掉，标签和输入框不会被拆开。 */}
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, whiteSpace: 'nowrap' }}>
            本次数量：<Input style={{ width: 130 }} aria-label="草稿数量" inputMode="decimal" value={String(row.quantity)} disabled={!editable} onChange={v => patch(index, { quantity: v })} />
          </span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, whiteSpace: 'nowrap' }}>
            单价：<Input style={{ width: 130 }} aria-label="草稿单价" inputMode="decimal" value={row.unit_price == null ? '' : String(row.unit_price)} placeholder="未核价" disabled={!editable} onChange={v => patch(index, { unit_price: v === '' ? null : v })} />
          </span>
        </div>
        {/* 只读时也是 TextArea，默认给的是多行大框：一行「1200×800×5mm」被塞进
            100px 高的空框里，空白比字还多（2026-11-20 截图）。autosize 按内容收高。 */}
        <p>本次规格</p><TextArea autosize={{ minRows: 1, maxRows: 4 }} value={row.specification ?? ''} disabled={!editable} onChange={v => patch(index, { specification: v })} />
        {(row.specification ?? '') !== (row.source_snapshot.specification ?? '') && <p>原规格：{row.source_snapshot.specification || '未记录'}</p>}
        <p>本次备注</p><TextArea autosize={{ minRows: 1, maxRows: 4 }} placeholder="没有就留空" value={row.remark ?? ''} disabled={!editable} onChange={v => patch(index, { remark: v })} />
      </div>)}
      <p>计划交期</p><DatePicker type="date" format="yyyy-MM-dd" showClear style={{ width: '100%' }} placeholder="选择计划交期" value={form.delivery_date ? new Date(form.delivery_date) : undefined} disabled={!editable} onChange={(_, v) => { markDirty(true); setForm({ ...form, delivery_date: (v as string) || '' }) }} />
      <p>付款条件</p><Input aria-label="付款条件" value={form.payment_terms} disabled={!editable} onChange={v => { markDirty(true); setForm({ ...form, payment_terms: v }) }} />
      <p>备注</p><TextArea value={form.remark} disabled={!editable} onChange={v => { markDirty(true); setForm({ ...form, remark: v }) }} />
      {/* 全页表单是灰的时候必须说清为什么（主人 2026-10-12：「点又点不了，又不给
          提示为什么点不了」）。这里只有一个判据 `editable`，三种原因分开写。 */}
      <BlockedHint blocked={[
        { ok: !busy, reason: '上一次操作还在进行中，稍等一下再改。' },
        { ok: draft.status === 'draft', reason: '这份草稿已经转成正式订单，草稿内容不能再改。' },
        { ok: can('order:manage'), reason: '你的账号没有「订单管理」权限，只能查看；需要修改请让有权限的同事来改，或请管理员开通。' },
      ]} action="修改草稿和保存" />
      {draft.status === 'draft' && can('order:manage') && <GuardedButton
          style={{ marginTop: 12 }}
          loading={save.isPending}
          disabled={!editable || !dirty}
          blockedReasons={!editable || !dirty ? ['草稿没有改动，不需要保存。'] : []}
          action="保存草稿"
          onClick={() => save.mutate()}
        >
          保存草稿
        </GuardedButton>}
    </SectionCard>
    <SectionCard title="确认正式下单" className="order-draft-confirm" accent="var(--crm-primary)">
      {draft.order_id ? (
        <Link to={`/orders/${draft.order_id}`}>查看正式订单</Link>
      ) : (
        <div className="order-draft-confirm-body">
          <p className="workflow-guide">
            选择同一客户、同一商机中客户已接受的报价。草稿的全部明细、价格和付款条件须与选定版本一致。
          </p>
          <div className="order-draft-quote-picker">
            <FormLabel required>下单依据</FormLabel>
            <Select
              aria-label="下单依据"
              placeholder="选择客户已确认报价"
              style={{ width: '100%' }}
              value={selectedQuote?.current_version_id ?? undefined}
              disabled={busy || !can('order:manage') || !can('quote:view') || quotes.isError || quoteOptions.length === 0}
              loading={quotes.isFetching}
              optionList={quoteOptions.map(quote => ({
                value: quote.current_version_id!,
                label: [quote.quote_no, quote.current_version_no ? `V${quote.current_version_no}` : null].filter(Boolean).join(' · '),
              }))}
              onChange={value => {
                setQuoteSelection({ draftId, versionId: Number(value) })
                confirm.reset()
              }}
            />
          </div>
          {selectedQuote && (
            <div className="order-draft-quote-summary">
              {quoteAmount != null && <span>报价金额 <strong>{formatMoney(quoteAmount, 'CNY')}</strong></span>}
              <Link to={`/quotes/${selectedQuote.id}?version=${selectedQuote.current_version_id}`}>查看选定报价明细</Link>
            </div>
          )}
          {quotes.isError && (
            <Banner type="danger" fullMode={false} closeIcon={null} description={quotes.error.message}>
              <Button size="small" onClick={() => void quotes.refetch()}>重新读取报价</Button>
            </Banner>
          )}
          {confirmationError && (
            <Banner
              type="danger"
              fullMode={false}
              closeIcon={null}
              title="下单未完成"
              description={draftMismatch
                ? '草稿与客户确认报价不一致。填错了就按报价改正草稿并保存。如果客户改了约定，请先更新报价并重新取得客户确认。'
                : confirmationError}
            >
              {draftMismatch && (
                <Button
                  size="small"
                  theme="borderless"
                  onClick={() => document.getElementById('order-draft-details')?.scrollIntoView({ block: 'start' })}
                >
                  回到草稿明细
                </Button>
              )}
            </Banner>
          )}
          <p className="workflow-guide">
            下单后会打开正式订单。系统先生成一条全款应收，可到订单的「应收计划」核对或拆分。
          </p>
          {confirmBlocked ? (
            <GuardedButton theme="outline" disabled action="正式下单" blockedReasons={[confirmBlocked]}>
              确认正式下单
            </GuardedButton>
          ) : (
            <Popconfirm
              title="确认正式下单？"
              content="确认后会生成正式订单并进入履约。请先核对明细、付款条件和计划交期。"
              onConfirm={() => {
                if (selectedQuote?.current_version_id) {
                  confirm.mutate({ id: draftId, revision: draft.revision, versionId: selectedQuote.current_version_id })
                }
              }}
            >
              <Button type="primary" theme="outline" loading={confirm.isPending}>确认正式下单</Button>
            </Popconfirm>
          )}
        </div>
      )}
    </SectionCard>
    <SectionCard title="草稿需求单" style={{ marginTop: 16 }}><p>生成文件会保存当时的草稿和差异；以后修改不会覆盖旧文件。</p>
      {can('order:manage') && <GuardedButton
          disabled={busy || dirty}
          blockedReasons={dirty ? ['草稿有新改动：请先点上面那张卡里的「保存草稿」，再生成需求单。'] : []}
          action="生成草稿需求单"
          loading={generate.isPending}
          onClick={() => generate.mutate()}
        >
          生成草稿需求单
        </GuardedButton>}
      {(docs.data ?? []).map(doc => <p key={doc.id}>{doc.doc_no} V{doc.version} · <a onClick={() => void showDownloadResult(downloadBizDoc(doc))}>下载 PDF</a></p>)}
    </SectionCard>
  </div>
}
