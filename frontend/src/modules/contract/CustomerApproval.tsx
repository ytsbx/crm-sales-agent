import { formatDingTalkTime } from '../../shared/beijing-time'
import { useEffect, useState } from 'react'
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, Select, Toast } from '@douyinfe/semi-ui'
import { api } from '../../shared/api/client'
import type { ContractDocument } from '../../shared/api/contract'

const LABELS: Record<string, string> = { RUNNING: '办理中', NEW: '待处理', COMPLETED: '已完成', TERMINATED: '已撤回', agree: '同意', refuse: '驳回', NONE: '未处理', AGREE: '同意', REFUSE: '驳回', FINISH: '已完成', FINISHED: '已完成', EXECUTE_TASK_NORMAL: '审批处理', EXECUTE_TASK_AGENT: '代理审批', START_PROCESS_INSTANCE: '发起审批', TERMINATE_PROCESS_INSTANCE: '撤回审批', ADD_REMARK: '评论', REDIRECT_PROCESS: '转交', COMMENT: '评论', START: '发起', PENDING: '待处理', CANCELLED: '已取消' }
function label(value: unknown) { return typeof value === 'string' ? LABELS[value] || '已记录' : '' }
export function ApprovalHistory({ result }: { result: Record<string, unknown> }) {
  const payload = (result.payload || {}) as Record<string, unknown>
  const tasks = Array.isArray(payload.tasks) ? payload.tasks as Record<string, unknown>[] : []
  const records = Array.isArray(payload.operationRecords) ? payload.operationRecords as Record<string, unknown>[] : []
  const time = (value: unknown) => typeof value === 'string' && !Number.isNaN(Date.parse(value)) ? formatDingTalkTime(value) : ''
  const person = (row: Record<string, unknown>) => String(row.userName || row.staffName || '未记录处理人姓名')
  return <div style={{ display: 'grid', gap: 8, marginTop: 8 }}>
    <b>审批节点</b>
    {tasks.length ? tasks.map((row, i) => <div key={i}>{person(row)}{row.activityName ? ` · ${String(row.activityName)}` : ''} · {label(row.taskStatus || row.status)} {label(row.taskResult || row.result)} · {time(row.finishTime || row.endTime || row.createTime)}</div>) : <div>历史记录没有节点明细</div>}
    <b>处理记录</b>
    {records.length ? records.map((row, i) => <div key={i}>{person(row)}{row.showName ? ` · ${String(row.showName)}` : ''} · {label(row.operationType || row.type || row.result)} · {time(row.date || row.createTime)}{row.remark ? ` · ${String(row.remark)}` : ''}</div>) : <div>暂无处理记录</div>}
  </div>
}

export function ContractBasis({ doc }: { doc: ContractDocument }) {
  const [choice, setChoice] = useState('')
  const [search, setSearch] = useState('')
  const [keyword, setKeyword] = useState('')
  const [selectedLabel, setSelectedLabel] = useState('')
  useEffect(() => {
    const timer = setTimeout(() => setKeyword(search.trim()), 250)
    return () => clearTimeout(timer)
  }, [search])
  useEffect(() => {
    // 换合同时清掉上一张单据的选择和搜索词，避免把 A 客户的依据带到 B 客户。
    setChoice('')
    setSearch('')
    setKeyword('')
    setSelectedLabel('')
  }, [doc.id])
  const cache = useQueryClient()
  // 业务依据改为服务端按单号搜索：订单搜 order_no，报价搜 quote_no/客户名。
  // 同一客户下的历史单据可能很多，不能只拉首页，也不把整家客户的单据全塞进前端。
  const choices = useInfiniteQuery({
    queryKey: ['contract-basis-search', doc.customer_id, keyword],
    initialPageParam: 1,
    queryFn: async ({ pageParam }) => {
      const params = {
        customer_id: doc.customer_id,
        keyword: keyword || undefined,
        page: pageParam,
        page_size: 10,
      }
      const [orders, quotes] = await Promise.all([
        api.get<{ items: { id: number; order_no: string; status: string }[]; total: number; page: number; page_size: number }>('/orders', params),
        api.get<{ items: { id: number; quote_no: string; status: string }[]; total: number; page: number; page_size: number }>('/quotes', params),
      ])
      return {
        items: [
          ...orders.items.filter((order) => order.status !== 'cancelled')
            .map((order) => ({ value: `order:${order.id}`, label: `订单 ${order.order_no}` })),
          ...quotes.items.filter((quote) => quote.status === 'sent' || quote.status === 'accepted')
            .map((quote) => ({ value: `quote:${quote.id}`, label: `报价 ${quote.quote_no}` })),
        ],
        hasMore:
          orders.page * orders.page_size < orders.total ||
          quotes.page * quotes.page_size < quotes.total,
      }
    },
    getNextPageParam: (last, pages) => (last.hasMore ? pages.length + 1 : undefined),
  })
  const options = choices.data?.pages.flatMap((page) => page.items) ?? []
  if (choice && !options.some((option) => option.value === choice)) {
    options.push({ value: choice, label: selectedLabel || '已选单据' })
  }
  const save = useMutation({ mutationFn: () => {
    const [kind, id] = choice.split(':')
    return api.post(`/contract-documents/${doc.id}/basis`, { [kind === 'order' ? 'order_id' : 'quote_id']: Number(id) })
  }, onSuccess: () => { Toast.success('业务依据已保存'); void cache.invalidateQueries({ queryKey: ['contract-detail', doc.id] }); void cache.invalidateQueries({ queryKey: ['contract-documents'] }) }, onError: (e: Error) => Toast.error(e.message) })
  return <div style={{ display: 'grid', gap: 8 }}>
    <div>归档前请补充正式订单或客户已收到的有效报价；可按单号搜索。</div>
    {/* 原生 <select> 的边框、字号、高度都和同页 Semi 控件对不上（V2-12），换成 Semi Select */}
    <Select
      aria-label="合同业务依据"
      placeholder={keyword ? '输入单号搜索' : '选择订单或报价，也可直接输入单号'}
      value={choice || undefined}
      onChange={(value) => {
        const key = String(value ?? '')
        setChoice(key)
        setSelectedLabel(options.find((option) => option.value === key)?.label ?? '')
      }}
      loading={choices.isFetching || search.trim() !== keyword}
      filter
      remote
      showClear
      onSearch={setSearch}
      optionList={options}
      style={{ width: '100%' }}
      outerBottomSlot={choices.isError ? (
        <Button theme="borderless" onClick={() => {
          if (choices.isFetchNextPageError) void choices.fetchNextPage()
          else void choices.refetch()
        }}>加载失败，点击重试</Button>
      ) : choices.hasNextPage ? (
        <Button theme="borderless" loading={choices.isFetchingNextPage} onClick={() => {
          if (!choices.isFetching && search.trim() === keyword) void choices.fetchNextPage()
        }}>加载更多</Button>
      ) : undefined}
    />
    <Button loading={save.isPending} onClick={() => { if (!choice) Toast.warning('请选择业务依据'); else save.mutate() }}>保存业务依据</Button>
    {choices.isSuccess && options.length === 0 && (
      <p className="workflow-guide">
        {keyword
          ? '没有匹配的订单或报价，换个单号试试。'
          : '可等客户收到正式报价后回来绑定，或从这位客户的正式订单中选择；也可以直接输入单号。'}
      </p>
    )}
  </div>
}
