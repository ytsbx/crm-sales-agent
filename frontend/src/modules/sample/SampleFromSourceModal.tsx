import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Checkbox, InputNumber, Modal, TextArea, Toast } from '@douyinfe/semi-ui'
import { createOrderDraft, getOrderDraftSource } from '../../shared/api/order'
import { createSampleFromSource, getSampleSource, type SampleSourceRef } from '../../shared/api/sample'
import { newRequestKey } from '../../shared/api/requestKey'
import BlockedHint, { useBlockedNotify } from '../../shared/components/BlockedHint'

type Draft = { selected: boolean; quantity: number; specification: string; remark: string; unit_price: number | null }

/**
 * 从询价 / 报价版本建订单草稿或打样申请。
 *
 * 2026-10-12 主人反馈（原话）：
 *   「这种我点又点不了，又不给提示为什么点不了，这种体验很不好，类似的情况都要修」
 * 截图里是「从来源建立订单草稿」：报价 Q202610120001 V1 是 2026-10-12 13:15 建的
 * **一条明细都没有**（库中 `quote_items` 为 0 行），于是 `items` 是空数组 ——
 * 没有明细就没有任何一行可勾选，`invalid` 恒为 true，确认按钮从打开那一刻就是灰的，
 * 而弹窗里一个字都没解释。
 *
 * 同一条弹窗还有第二种「灰得没道理」的形态：定制需求**没记采购数量**（`quantity`
 * 留空是合法状态，schema 里写明「留空仍是尚未确定」），数量被初始化成 NaN，
 * 数字框显示空，按钮同样直接变灰。这种情况光看界面也猜不出差在哪。
 *
 * 修法只有一条原则：**按钮可以继续灰着，但界面必须说清为什么、以及怎么才能继续**。
 * 所以下面每一条 `disabled` 的判据都在 `blocked` 里有一条对应的、带下一步动作的文案，
 * 行内还会就近指出是哪一行缺数量。判据本身（数量必须为正）没有放松。
 */
export default function SampleFromSourceModal({ source, onClose, mode = 'sample' }: { source: SampleSourceRef; onClose: () => void; mode?: 'sample' | 'order' }) {
  const navigate = useNavigate()
  const client = useQueryClient()
  const [drafts, setDrafts] = useState<Record<number, Draft>>({})
  const requestKey = useRef('')
  if (!requestKey.current) requestKey.current = newRequestKey()
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
    requestKey.current = newRequestKey()
    setDrafts(old => ({ ...old, [id]: { ...old[id], ...patch } }))
  }
  const rows = query.data?.items ?? []
  const rowsWithDraft = rows.map(row => ({ row, draft: drafts[row.source_item_id] })).filter(({ draft }) => Boolean(draft))
  const selected = rowsWithDraft.filter(({ draft }) => draft.selected)
  /** 没填数量的行（来源没记数量时就是这种）。行内与页脚用的是**同一个判据**。 */
  const quantityMissing = selected.filter(({ draft }) => !Number.isFinite(draft.quantity) || !(draft.quantity > 0))
  const actionText = mode === 'order' ? '建立订单草稿' : '建立打样申请'
  const quantityFieldText = mode === 'order' ? '本次下单数量' : '本次样品数量'
  // 两条「一条明细都没有」的说法分工不同，不要混：短句进页脚清单，长句在正文里。
  const noRowsShort = mode === 'order'
    ? '这个来源版本一条明细都没有，没有可建草稿的明细。'
    : '这个来源版本一条明细都没有，没有可打样的明细。'
  const noRowsDetail = mode === 'order'
    ? '请回到报价详情补上明细，或用报价中心「从定制需求发起报价」生成带明细的版本；也可以换一个有明细的历史版本。'
    : '请回到询价/报价补上明细，或换一个有明细的历史版本，再回来申请打样。'
  const selectShort = '至少要勾选一行明细；现在一行都没勾。'
  const quantityShort = `有 ${quantityMissing.length} 行没有填${quantityFieldText}（数量必须大于 0）；来源没记原采购数量的行需要你手动填。`
  /**
   * 按钮为什么不能点 —— 这是**唯一判据**，同时供三处使用：
   * ① 按钮的 `disabled`；② 点说明时弹出的提示条（`notifyBlocked`）；③ 页脚那段常驻说明。
   *
   * ⚠️ 数据还在路上时（`query.data` 为空）**不算原因**：那时 `rows` 是空数组，
   * 照算会得出「至少要勾选一行明细」—— 那是一句假话（明细还没读出来而已），
   * 实测第一版就闪了这么一句出去。没数据时界面只说「读取来源资料中…」。
   */
  const blockedReasons = query.data
    ? [
        ...(rows.length === 0 ? [noRowsShort] : []),
        ...(selected.length === 0 ? [selectShort] : []),
        ...(quantityMissing.length > 0 ? [quantityShort] : []),
      ]
    : []
  /**
   * 页脚那段常驻说明要列哪几句。
   *
   * 只有一种情况要减一句：**一条明细都没有**时，正文里那段已经把「为什么 + 怎么办」
   * 写全了（点名去报价详情补明细），页脚不必把同一句话再说一遍；顺带也去掉
   * 「至少要勾选一行明细」—— 一行都没有，用户根本勾不到东西，那句话是死胡同。
   *
   * ⚠️ 这里减哪几句必须按**状态**判断，**不能**按「文案等于哪一句」去过滤 ——
   * 第一版就是那么写的（`blockedReasons.filter(text => text !== noRowsShort)`），
   * 实测翻车：过滤按**字符串值**匹配，一句没对上就悄悄不过滤，页脚于是对着一份
   * **一条明细都没有**的来源说「至少要勾选一行明细；现在一行都没勾」，而用户根本
   * 勾不到任何东西（浏览器实测见 `修复证据-2026-10-12-第四轮/`：那次 `data-debug`
   * 打出 `rows=0;selected=0;footer=1`，就是这么抓出来的）。
   * **文案是给人看的，不能当判据用 —— 判据只能是状态。**
   */
  const bannerReasons = rows.length === 0 ? [noRowsShort, selectShort] : []
  const footerReasons = blockedReasons.filter(text => !bannerReasons.includes(text))
  // ① 看得见（页脚正上方常驻灰字）+ ② 点得动（点那段说明就弹一句同样的原因），
  // 见 BlockedHint。这里**没有**给确认键加 pointerEvents 之类的花招：弹窗的
  // footer 在 portal 里，外面包一层收不到点击，硬做要改 footer 结构（风险大、收益小），
  // 而「点那段说明有反应」已经实打实成立（浏览器实测见证据目录）。
  const notifyBlocked = useBlockedNotify(blockedReasons, actionText)
  const okBlocked = !query.data || blockedReasons.length > 0
  return <Modal visible title={mode === 'order' ? '从来源建立订单草稿' : '从来源申请打样'} width={720} confirmLoading={mutation.isPending}
    okText={actionText}
    okButtonProps={{ disabled: okBlocked }}
    cancelButtonProps={{ disabled: mutation.isPending }} maskClosable={!mutation.isPending}
    onCancel={() => { if (!sending.current) onClose() }} onOk={() => {
      if (!sending.current && blockedReasons.length === 0 && query.data) { sending.current = true; mutation.mutate() }
    }}>
    {query.isPending && <p>读取来源资料中…</p>}
    {query.error && <p role="alert">{query.error.message}</p>}
    {query.data && <>
      <p>来源：{query.data.source.no} V{query.data.source.version}{query.data.source.is_historical ? '（历史版本）' : ''} · 客户：{query.data.customer_name}</p>
      <p>{mode === 'order' ? '草稿仅供提前准备，不进入生产、不生成应收、不计成交。正式下单前须核对客户确认报价；来源原数量不改。' : '原采购数量保留不变；本次样品数量默认 1 件，可单独修改。草稿与历史版本均可使用。'}</p>
      {/* 一条明细都没有是**确认按钮灰着的头号原因**（主人截图那一屏就是它），
          所以正文里就把「为什么 + 怎么办」说全，不留到页脚去猜。
          这段文字同时也是「点得动」的落点：确认键是灰的（原生 disabled 不派发点击），
          用户会去点这段说明 —— 点它就弹一句同样的原因（`notifyBlocked`），
          而不是石沉大海。 */}
      {rows.length === 0 && <div className="blocked-hint" role="alert" onClick={notifyBlocked} style={{ cursor: 'pointer' }}>
        <div className="blocked-hint-lead">这份来源里没有可{actionText === '建立订单草稿' ? '下单' : '打样'}的明细</div>
        <div>{noRowsDetail}</div>
      </div>}
      {rowsWithDraft.map(({ row, draft }) => {
        const missing = draft.selected && (!Number.isFinite(draft.quantity) || !(draft.quantity > 0))
        const quantityId = `source-quantity-${mode}-${row.source_item_id}`
        return <div key={row.source_item_id} style={{ borderTop: '1px solid var(--crm-border)', padding: '12px 0' }}>
          <Checkbox checked={draft.selected} disabled={mutation.isPending} onChange={e => change(row.source_item_id, { selected: Boolean(e.target.checked) })}>{row.name}</Checkbox>
          <p>原采购数量：{row.original_quantity == null ? '未记录' : Number(row.original_quantity).toLocaleString('zh-CN')}</p>
          <div>{quantityFieldText}：<InputNumber id={quantityId} min={0.001} precision={3} value={Number.isFinite(draft.quantity) ? draft.quantity : undefined}
            placeholder={row.original_quantity == null ? '来源未记录，请填写' : undefined}
            aria-invalid={missing || undefined} aria-describedby={missing ? `${quantityId}-required` : undefined}
            disabled={mutation.isPending || !draft.selected} onChange={v => change(row.source_item_id, { quantity: Number(v) })} /></div>
          {/* 行内提示：数量为空时数字框就是一个空白框，只说「必填」不够 ——
              要说清为什么空（来源未记录），以及不填就走不下去。 */}
          {missing && <p id={`${quantityId}-required`} className="blocked-hint" style={{ marginTop: 8 }}>
            {row.original_quantity == null
              ? `来源没有记录原采购数量，请填写${quantityFieldText}（必须大于 0）后再${actionText}。`
              : `请填写${quantityFieldText}（必须大于 0）后再${actionText}。`}
          </p>}
          {mode === 'order' && <div style={{ marginTop: 8 }}>草稿单价：<InputNumber min={0} precision={4} value={draft.unit_price ?? undefined} placeholder="未核价" disabled={mutation.isPending || !draft.selected} onChange={v => change(row.source_item_id, { unit_price: v === '' || v == null ? null : Number(v) })} /></div>}
          <p>本次规格</p><TextArea value={draft.specification} disabled={mutation.isPending || !draft.selected} onChange={v => change(row.source_item_id, { specification: v })} />
          {draft.specification !== (row.specification ?? '') && <p>原规格：{row.specification || '未记录'}</p>}
          <p>本次备注</p><TextArea value={draft.remark} disabled={mutation.isPending || !draft.selected} onChange={v => change(row.source_item_id, { remark: v })} />
        </div>
      })}
      {/* 页脚正上方：确认按钮灰着的时候，原因必须在同一屏里看得见。
          不传 loading —— 上面那行「读取来源资料中…」已经在说同一件事了。
          这段文字本身也是「点得动」的落点：灰按钮不吃点击，用户自然会点在
          它旁边这行字上（点了由 notifyBlocked 弹一句同样的原因）。 */}
      {footerReasons.length > 0 && <div className="blocked-hint-click" style={{ display: 'block' }} onClick={notifyBlocked}>
        <BlockedHint reasons={footerReasons} action={actionText} style={{ marginTop: 16 }} />
      </div>}
    </>}
  </Modal>
}
