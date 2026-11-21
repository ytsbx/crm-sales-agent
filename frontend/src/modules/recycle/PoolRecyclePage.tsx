import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Checkbox, Pagination, Popconfirm, Table, Tabs, Tag, Toast } from '@douyinfe/semi-ui'

import PageHeader from '../../shared/components/PageHeader'
import HelpTip from '../../shared/components/HelpTip'
import SectionCard from '../../shared/components/SectionCard'
import { useNotePrompt } from '../../shared/components/NotePromptModal'
import { formatBeijingDateTime, parseBusinessTime } from '../../shared/beijing-time'
import { usePermissions } from '../../shared/hooks/permissions'
import { emptyText } from '../../shared/hooks/emptyText'
import {
  batchDecideRecycleCandidates,
  decideRecycleCandidate,
  listRecycleCandidates,
  restoreRecycleCandidate,
  type RecycleCandidateRow,
  type RecycleStatus,
} from '../../shared/api/settings'
import './PoolRecyclePage.css'

/** 时间显示成"2026-10-06 12:30"（复核要看具体哪天）。空值给个短横。 */
function fmtDay(value: string | null | undefined): string {
  return formatBeijingDateTime(value)
}

const STATUS_LABEL: Record<RecycleStatus, string> = {
  pending: '待复核',
  deferred: '已暂缓',
  executed: '已回收',
  rejected: '已驳回',
}

const REVIEW_GUIDE: Record<RecycleStatus, string> = {
  // 待复核这一页上面已经有「扫描只提名，批准才真回收；未满等待期要走「提前回收」」
  // 那一行，再复述一遍怎么操作就是同一句话说了两遍，还把表格又往下压了一行（2026-11-21 巡检）。
  pending: '',
  deferred: '暂缓保留原候选。到「最早可回收」的时间后，可以决定是否批准；也可以随时驳回结案。',
  executed: '符合恢复条件时，有权限的同事可以把客户交还原负责人。',
  rejected: '驳回已结案。满 30 天后，系统按客户当时的跟进和业务情况重新判断。',
}

/**
 * 公海回收（2026-11-19 从「系统设置 · 业务规则」独立出来）。
 *
 * 原来这一整块（4 个页签 + 批量批准 / 暂缓 / 驳回 + 逐条复核）缩在系统设置里，
 * 主管要进系统设置才能干日常活儿 —— 那是配置页，不是作业页。主人拍板：
 * 复核搬成「销售作业」下一个独立入口（和线索中心、客户中心并列），
 * 系统设置只留下规则配置（天数、保护期、扫描按钮）。
 *
 * 这里只做"看候选 + 做决定"，不改任何规则；规则改动仍在系统设置。
 */
export function PoolRecyclePanel() {
  const notePrompt = useNotePrompt()
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const canRestore = can('customer:pool_review') && can('customer:pool_restore')

  // 1024 下六列排不开（会横向滚动），窄屏把「原负责人 / 最近有效联系 / 保护事项」
  // 三列收起来：复核时主要看客户、最早可回收、以及三个动作。
  /* 规则说明默认收起（见下方注释） */
  const [rulesOpen, setRulesOpen] = useState(false)
  const [compactLayout, setCompactLayout] = useState(() => window.innerWidth <= 680)
  const [narrowLayout, setNarrowLayout] = useState(
    () => typeof window !== 'undefined' && window.innerWidth <= 1280,
  )
  useEffect(() => {
    const onResize = () => {
      setNarrowLayout(window.innerWidth <= 1280)
      setCompactLayout(window.innerWidth <= 680)
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // 四个状态页签：待复核 / 已暂缓 / 已回收 / 已驳回。
  // 只查 pending 的话，"暂缓"一点记录就从页面消失、已回收的也找不回来（返修单第六批第 10 条）。
  const [candidatePage, setCandidatePage] = useState(1)
  const [candidatePageSize, setCandidatePageSize] = useState(20)
  const [candidateStatus, setCandidateStatus] = useState<RecycleStatus>('pending')
  const [selectedCandidateIds, setSelectedCandidateIds] = useState<number[]>([])
  const [receipt, setReceipt] = useState<RecycleCandidateRow | null>(null)

  const candidatesQuery = useQuery({
    queryKey: ['recycle-candidates', candidateStatus, candidatePage, candidatePageSize],
    queryFn: () =>
      listRecycleCandidates({
        status: candidateStatus,
        page: candidatePage,
        page_size: candidatePageSize,
      }),
  })

  const refreshCandidates = () => {
    void queryClient.invalidateQueries({ queryKey: ['recycle-candidates'] })
    void queryClient.invalidateQueries({ queryKey: ['customers'] })
  }

  const decideMutation = useMutation({
    mutationFn: ({ candidate, decision, note, early }: {
      candidate: RecycleCandidateRow
      decision: 'approve' | 'reject' | 'defer'
      note?: string
      early?: boolean
    }) => decideRecycleCandidate(candidate.id, decision, note, early),
    onSuccess: (row, { candidate }) => {
      setReceipt({ ...row, customer_name: candidate.customer_name, owner_name: candidate.owner_name })
      setSelectedCandidateIds((ids) => ids.filter((id) => id !== candidate.id))
      void queryClient.invalidateQueries({ queryKey: ['customer', candidate.customer_id] })
      Toast.success(row.status === 'rejected' ? '已驳回，可在「已驳回」查看再次评估时间。' :
        row.status === 'deferred' ? '已暂缓，到期后在「已暂缓」继续复核。' :
          row.status === 'executed' ? '客户已进入公海，可在「已回收」查看。' : row.status_label)
      refreshCandidates()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  /**
   * 批量复核结果：**逐条**记住哪条成、哪条为什么没成。
   * 只弹一句"已处理 N 条"是不够的 —— 被拦下的那条还留在待复核里，
   * 页面不列出来，主管会以为都办完了（返修单第六批第 10 条）。
   */
  const [batchResult, setBatchResult] = useState<{
    names: Map<number, string | null | undefined>
    done: { candidate_id: number; status: string }[]
    failed: { candidate_id: number; reason: string; code: number }[]
  } | null>(null)

  const batchMutation = useMutation({
    mutationFn: (payload: {
      decision: 'approve' | 'reject' | 'defer'
      note?: string
      early?: boolean
    }) =>
      batchDecideRecycleCandidates({ candidateIds: selectedCandidateIds, ...payload }),
    onSuccess: (data) => {
      setReceipt(null)
      setBatchResult({ ...data, names: new Map(candidatesQuery.data?.items.map((row) => [row.id, row.customer_name])) })
      setSelectedCandidateIds([])
      if (data.failed.length) {
        Toast.warning(
          `已处理 ${data.done.length} 条，${data.failed.length} 条未处理（下方逐条列了原因）`,
        )
      } else {
        Toast.success(`已处理 ${data.done.length} 条`)
      }
      refreshCandidates()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const restoreMutation = useMutation({
    mutationFn: ({ candidate, note }: { candidate: RecycleCandidateRow; note?: string }) => restoreRecycleCandidate(candidate.id, note),
    onSuccess: (row, { candidate }) => {
      setReceipt({ ...row, customer_name: candidate.customer_name, owner_name: candidate.owner_name })
      void queryClient.invalidateQueries({ queryKey: ['customer', candidate.customer_id] })
      Toast.success('客户已恢复给原负责人')
      refreshCandidates()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  /** 这条到「最早可回收时间」了吗？没到就只能走「提前回收」例外。 */
  const isDue = (row: RecycleCandidateRow) => {
    if (!row.earliest_action_at) return true
    const at = new Date(row.earliest_action_at).getTime()
    return Number.isNaN(at) || Date.now() >= at
  }

  /** 批准/驳回/暂缓。批准和暂缓可能要填说明（尤其"仍有保护但确需回收"时必须填）。 */
  const decide = async (
    decision: 'approve' | 'reject' | 'defer',
    row: RecycleCandidateRow,
    options?: { early?: boolean },
  ) => {
    const early = options?.early ?? false
    const label = early
      ? '提前回收'
      : { approve: '批准回收', reject: '驳回', defer: '暂缓' }[decision]
    const protected_ = (row.protection ?? []).length > 0
    const needNote = decision !== 'approve' || protected_ || early
    const note = await notePrompt.ask({
      title: `${label}「${row.customer_name ?? `客户 ${row.customer_id}`}」`,
      label: `${label}原因`,
      description: early
        ? `确认后立即回收，早于原定时间 ${fmtDay(row.earliest_action_at)}。请说明提前回收的依据。`
        : decision === 'approve' && protected_
          ? `当前记录有履约事项（${(row.protection ?? []).join('、')}）。确需例外回收，请说明依据。`
          : decision === 'approve' ? '符合条件的客户会立即进入公海。若系统提示有新的履约保护，核对后确需例外回收，可补填依据。'
            : decision === 'reject' ? '客户继续由原负责人负责。本次复核结案，30 天内系统不会重新提名。'
              : '客户继续由原负责人负责。到「已暂缓」查看具体可批准时间。',
      placeholder: decision === 'approve'
        ? '例如：已核实客户结束合作，同意回收'
        : '例如：客户明确表示需要延长跟进周期',
      required: needNote,
      maxLength: 255,
      tone: early || (decision === 'approve' && protected_) ? 'warning' : 'normal',
      okText: early ? '确认提前回收' : decision === 'approve' ? '确认回收' : `确认${label}`,
    })
    if (note === null) return
    decideMutation.mutate({ candidate: row, decision, note, early })
  }

  /** 单条恢复：把已回收的客户还给原负责人（已被别人领走时会报冲突）。 */
  const restore = async (row: RecycleCandidateRow) => {
    const note = await notePrompt.ask({
      title: `恢复「${row.customer_name ?? `客户 ${row.customer_id}`}」给原负责人`,
      label: '恢复原因',
      description: '客户会从公海回到原负责人名下，其他同事不再能领取。',
      placeholder: '例如：客户回访后确认继续合作',
      required: true,
      maxLength: 255,
      okText: '确认恢复',
    })
    if (note === null) return
    restoreMutation.mutate({ candidate: row, note })
  }

  function showCandidateStatus(status: RecycleStatus) {
    setCandidateStatus(status)
    setCandidatePage(1)
    setSelectedCandidateIds([])
    setBatchResult(null)
    setReceipt(null)
  }

  const busy = decideMutation.isPending || restoreMutation.isPending || batchMutation.isPending
  const isReviewQueue = candidateStatus === 'pending' || candidateStatus === 'deferred'
  const pagination = {
    currentPage: candidatePage,
    pageSize: candidatePageSize,
    total: candidatesQuery.data?.total ?? 0,
    onPageChange: setCandidatePage,
    showSizeChanger: true,
    pageSizeOpts: [10, 20, 50, 100],
    onPageSizeChange: (size: number) => {
      setCandidatePageSize(size)
      setCandidatePage(1)
    },
  }

  function candidateActions(row: RecycleCandidateRow) {
    if (candidateStatus === 'executed') {
      return row.restored_at ? <Tag size="small" color="green">已恢复</Tag>
        : canRestore ? <Button size="small" theme="borderless" disabled={busy} onClick={() => void restore(row)}>恢复</Button>
          : '—'
    }
    if (candidateStatus === 'rejected') {
      const at = parseBusinessTime(row.reconsider_at)
      return at ? at.getTime() > Date.now() ? `${fmtDay(row.reconsider_at)} 后` : '已到复评时间' : '已结案'
    }
    const due = isDue(row)
    return (
      <span className="recycle-review-actions">
        <Button theme={due ? 'solid' : 'outline'} type="primary" size="small" disabled={busy}
          onClick={() => void decide('approve', row, { early: !due })}>{due ? '批准回收' : '提前回收'}</Button>
        <Button theme="borderless" size="small" disabled={busy} onClick={() => void decide('defer', row)}>暂缓</Button>
        <Button type="danger" theme="borderless" size="small" disabled={busy} onClick={() => void decide('reject', row)}>驳回</Button>
      </span>
    )
  }

  const receiptStatus = receipt?.status === 'executed' || receipt?.status === 'deferred' || receipt?.status === 'rejected'
    ? receipt.status : undefined
  const empty = emptyText(candidatesQuery, `没有${STATUS_LABEL[candidateStatus]}的候选`,
    candidateStatus === 'pending' ? '符合条件的客户在生成回收预告后会出现在这里。' : '切到「待复核」可查看新的候选。')

  return (
    <>
      {notePrompt.node}
      {/* 这里不再自带 SectionCard / 页头：这一块现在挂在「客户中心」的第二个页签里，
          外层已经有一张卡（独立路由 /pool-recycle 也复用它，壳在外面补）。 */}
      <div className="recycle-panel">
        <div>
          {/* 这段说明原来写在系统设置里，跟着搬过来：它讲的是"怎么干这个活"，
              不是"规则怎么配"。 */}
          {/* 这段规则原来平铺三行、每天开场先占掉 60~70px，把真正要看的表格往下压。
              规则是一次性看懂的，待办是每天都变的：默认只留一句「最常违反的那条」，
              其余收进「规则说明」，点开才展开。（2026-11-19 巡检） */}
          <div
            style={{ color: 'var(--crm-text-2)', fontSize: 12, lineHeight: 1.65, marginBottom: 12 }}
          >
            扫描只提名，批准才真回收；未满等待期要走「提前回收」并填原因。
            <button
              type="button"
              className="workflow-help-toggle"
              aria-expanded={rulesOpen}
              onClick={() => setRulesOpen((v) => !v)}
            >
              {rulesOpen ? '收起规则说明' : '查看规则说明'}
            </button>
            {rulesOpen && (
              <div style={{ marginTop: 6, paddingLeft: 10, borderLeft: '2px solid var(--crm-surface-high)' }}>
                · 批准前<b>必须等预告期 / 暂缓期满</b>；没满只能走「提前回收」（例外动作，要填原因）。<br />
                · 随时可以驳回结案。30 天内不会重新提名，到期后按当时的情况重新判断。<br />
                · 有在途订单、未结应收、有效正式报价或在途打样时，确需回收要填写例外原因。
              </div>
            )}
          </div>
          <Tabs
            type="button"
            tabBarClassName="recycle-review-tabs"
            activeKey={candidateStatus}
            onChange={(key) => showCandidateStatus(key as RecycleStatus)}
            tabList={[
              { tab: '待复核', itemKey: 'pending' },
              { tab: '已暂缓', itemKey: 'deferred' },
              { tab: '已回收', itemKey: 'executed' },
              { tab: '已驳回', itemKey: 'rejected' },
            ]}
            style={{ marginBottom: 12 }}
          />
          {REVIEW_GUIDE[candidateStatus] && (
            <p className="workflow-guide">
              {REVIEW_GUIDE[candidateStatus]}{' '}
              {candidateStatus === 'pending' && can('settings:manage') && (
                <Link to="/settings?tab=rules&section=pool">查看扫描和回收规则</Link>
              )}
            </p>
          )}
          {receipt && (
            <div className="recycle-review-receipt" role="status">
              <div>
                <strong>{receipt.customer_name ?? `客户 #${receipt.customer_id}`} · {receipt.restored_at ? '已恢复' : receipt.status_label}</strong>
                {receipt.status === 'deferred' && <div>下次可批准时间 {fmtDay(receipt.earliest_action_at)}</div>}
                {receipt.status === 'rejected' && <div>可再次评估时间 {fmtDay(receipt.reconsider_at)}</div>}
                {receipt.restored_at && <div>已交还{receipt.owner_name ?? '原负责人'}</div>}
                {(receipt.restore_note || receipt.decision_note) && <div>{receipt.restore_note || receipt.decision_note}</div>}
              </div>
              {receiptStatus && receiptStatus !== candidateStatus && (
                <Button size="small" theme="outline" onClick={() => showCandidateStatus(receiptStatus)}>
                  查看{STATUS_LABEL[receiptStatus]}
                </Button>
              )}
            </div>
          )}
          {isReviewQueue && compactLayout && !selectedCandidateIds.length && Boolean(candidatesQuery.data?.items.length) && (
            <p className="recycle-batch-hint">勾选客户后可批量处理。</p>
          )}
          {isReviewQueue && (!compactLayout || selectedCandidateIds.length > 0) && (
            <div className="recycle-batch-toolbar">
              <span style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
                已选 {selectedCandidateIds.length} 条
              </span>

              {/* 批量决定一次影响多个人/多个客户，点一下立刻生效太轻
                  （主人 2026-10-06 定的范围：批量与不可逆的动作都要确认）。 */}
              <Popconfirm
                title={`批准选中的 ${selectedCandidateIds.length} 条回收候选？`}
                content="符合条件的客户会立即进入公海。未到可回收时间或仍受保护的客户会留在复核名单中。"
                onConfirm={() => batchMutation.mutate({ decision: 'approve' })}
              >
                <Button
                  size="small"
                  disabled={!selectedCandidateIds.length || busy}
                  loading={batchMutation.isPending}
                >
                  批量批准
                </Button>
              </Popconfirm>
              <Popconfirm
                title={`暂缓选中的 ${selectedCandidateIds.length} 条回收候选？`}
                content="保留原候选，暂缓到期后继续复核。"
                onConfirm={() => batchMutation.mutate({ decision: 'defer' })}
              >
                <Button
                  size="small"
                  disabled={!selectedCandidateIds.length || busy}
                  loading={batchMutation.isPending}
                >
                  批量暂缓
                </Button>
              </Popconfirm>
              <Popconfirm
                title={`驳回选中的 ${selectedCandidateIds.length} 条回收候选？`}
                content="结束本次复核，30 天内系统不会重新提名这些客户。"
                onConfirm={() => batchMutation.mutate({ decision: 'reject' })}
              >
                <Button
                  size="small"
                  type="danger"
                  disabled={!selectedCandidateIds.length || busy}
                  loading={batchMutation.isPending}
                >
                  批量驳回
                </Button>
              </Popconfirm>
            </div>
          )}
          {batchResult && (
            <div
              style={{
                fontSize: 12,
                marginBottom: 8,
                padding: '8px 10px',
                background: 'var(--crm-surface-low)',
                borderRadius: 6,
              }}
            >
              <div>成功 {batchResult.done.length} 条；未处理 {batchResult.failed.length} 条。</div>
              {batchResult.failed.map((row) => (
                <div key={row.candidate_id} style={{ color: 'var(--crm-danger, #d45)' }}>
                  {batchResult.names.get(row.candidate_id) ?? `候选 #${row.candidate_id}`} · {row.reason}
                </div>
              ))}
            </div>
          )}
          {compactLayout ? (
            candidatesQuery.isPending ? <div role="status">正在读取回收记录…</div>
              : !candidatesQuery.data?.items.length ? empty
                : <>
                  <ul className="recycle-mobile-list">
                    {candidatesQuery.data.items.map((row) => (
                      <li key={row.id} className="recycle-mobile-item">
                        <div className="recycle-mobile-heading">
                          <Link to={`/customers/${row.customer_id}`}>{row.customer_name ?? `客户 #${row.customer_id}`}</Link>
                          {isReviewQueue && <Checkbox aria-label={`选择${row.customer_name ?? row.id}`}
                            checked={selectedCandidateIds.includes(row.id)}
                            onChange={(event) => {
                              const checked = event.target.checked
                              setSelectedCandidateIds((ids) => checked ? [...ids, row.id] : ids.filter((id) => id !== row.id))
                            }} />}
                        </div>
                        <div>原负责人 {row.owner_name ?? '未记录'}{row.level ? ` · ${row.level} 级` : ''}</div>
                        {isReviewQueue && <>
                          <div>最近活跃 {fmtDay(row.last_active_at)}</div>
                          <div>最早可回收 {fmtDay(row.earliest_action_at)}</div>
                          {row.protection?.length > 0 && <div>提名时的保护事项 {row.protection.join('、')}</div>}
                        </>}
                        {candidateStatus === 'rejected' && <div>可再次评估 {fmtDay(row.reconsider_at)}</div>}
                        {candidateStatus === 'executed' && <div>回收时间 {fmtDay(row.executed_at)}</div>}
                        {(row.restore_note || row.decision_note) && <div>{row.restore_note || row.decision_note}</div>}
                        {candidateStatus !== 'rejected' && candidateActions(row)}
                      </li>
                    ))}
                  </ul>
                  {(pagination.total > pagination.pageSize || candidatePageSize !== 20) && (
                    <Pagination {...pagination} size="small" className="recycle-mobile-pagination" />
                  )}
                </>
          ) : (
          <Table<RecycleCandidateRow>
            rowKey="id"
            className="recycle-cand-table"
            loading={candidatesQuery.isLoading}
            dataSource={candidatesQuery.data?.items ?? []}
            pagination={pagination}
            rowSelection={
              candidateStatus === 'pending' || candidateStatus === 'deferred'
                ? {
                    selectedRowKeys: selectedCandidateIds,
                    onChange: (keys) => setSelectedCandidateIds((keys ?? []) as number[]),
                  }
                : undefined
            }
            empty={empty}
            columns={[
              {
                title: '客户',
                dataIndex: 'customer_name',
                width: 236,
                // 客户名带「【演示造数】」这类长后缀，加上「A 级 · 超 30 天未跟进」
                // 挤在同一行会把整张表撑出横向滚动条。副信息单独一行，客户列就能收窄。
                render: (v: string | null, r: RecycleCandidateRow) => (
                  <div style={{ lineHeight: 1.5 }}>
                    <div className="recycle-cand-name">{v ?? `客户编号 ${r.customer_id}`}</div>
                    <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
                      {r.level ? `${r.level} 级` : ''}
                      {r.rule_days ? ` · 超 ${r.rule_days} 天未跟进` : ''}
                    </div>
                  </div>
                ),
              },
              ...(narrowLayout
                ? []
                : [
                    {
                      title: '原负责人',
                      dataIndex: 'owner_name',
                      width: 80,
                      render: (v: string | null) => v ?? '-',
                    },
                  ]),
              // 1024 下（内容区约 675）这一列和三个操作链接放不在一行，
              // 最右的「驳回」永远差十几像素被裁。窄屏收起它。
              ...(narrowLayout
                ? []
                : [
                    {
                      title: '最近有效联系',
                      dataIndex: 'last_active_at',
                      width: 150,
                      render: (v: string | null) => fmtDay(v),
                    },
                  ]),
              ...(candidateStatus === 'executed' || candidateStatus === 'rejected'
                ? [
                    {
                      title: '处理时间',
                      dataIndex: 'decided_at',
                      width: 140,
                      render: (v: string | null) => fmtDay(v),
                    },
                    {
                      title: '说明',
                      dataIndex: 'decision_note',
                      render: (v: string | null) => v ?? '-',
                    },
                  ]
                : [
                    ...(narrowLayout
                      ? []
                      : [
                          {
                            title: (
                              <span className="crm-help-label">
                                保护事项
                                <HelpTip forLabel="保护事项" text="客户名下还在进行的合同、订单等履约事项。有保护事项的客户通常不回收；超过最长保护期仍可能被提名。" />
                              </span>
                            ),
                            dataIndex: 'protection',
                            width: 72,
                            render: (list: string[], r: RecycleCandidateRow) =>
                              (list ?? []).length === 0 ? (
                                <span style={{ color: 'var(--crm-text-3)' }}>无</span>
                              ) : (
                                <>
                                  {/* 有保护却仍被提名 = 保护已过最长有效期（后端下发标记） */}
                                  {r.protection_overridden && (
                                    <div style={{ marginBottom: 4 }}>
                                      <Tag color="orange" size="small">
                                        有履约事项仍被提名，请重点看
                                      </Tag>
                                    </div>
                                  )}
                                  <span style={{ color: 'var(--crm-warning)' }}>
                                    {(list ?? []).join('；')}
                                  </span>
                                </>
                              ),
                          },
                        ]),
                    {
                      title: (
                        <span className="crm-help-label">
                          最早可回收
                          <HelpTip forLabel="最早可回收" text="这条候选最早能被批准回收的日期。预告期和主管暂缓期取较晚的那个，两个都过了才能批。" />
                        </span>
                      ),
                      dataIndex: 'earliest_action_at',
                      width: narrowLayout ? 130 : 132,
                      render: (v: string | null, r: RecycleCandidateRow) =>
                        v ? (
                          /* 原来"还没到期"整格染橙（warning）：一屏十条全在预告期，
                             整列橙色反而看不出哪一条已经可以批了。
                             改成日期一律深色主标识（和表里其他"日期/编号"一致），
                             没到期只由下面那行小灰字「（预告期中）」说明 ——
                             颜色不承担判断，文字承担。（2026-11-20 巡检） */
                          <div style={{ lineHeight: 1.5 }}>
                            <div style={{ color: 'var(--crm-text-1)', fontWeight: 500 }}>{fmtDay(v)}</div>
                            {!isDue(r) && (
                              <div style={{ fontSize: 11, color: 'var(--crm-text-3)' }}>
                                （{r.status === 'deferred' ? '暂缓期' : '预告期'}中）
                              </div>
                            )}
                            {r.status === 'deferred' && r.decision_note && (
                              <div style={{ fontSize: 12, overflowWrap: 'anywhere' }}>{r.decision_note}</div>
                            )}
                          </div>
                        ) : (
                          '-'
                        ),
                    },
                  ]),
              {
                title: candidateStatus === 'rejected' ? '再次评估' : '操作',
                width: candidateStatus === 'executed' ? 90 : 172,
                render: (_: unknown, row: RecycleCandidateRow) => candidateActions(row),
              },
            ]}
          />
          )}
        </div>
      </div>
    </>
  )
}

/**
 * 独立路由 /pool-recycle 的壳：同一块复核，包一张卡 + 一个页头。
 * 主入口在「客户中心」的页签里（菜单不再单列一项），这里只为老链接兜底。
 */
export function PoolRecyclePage() {
  return (
    <div className="page-container">
      <PageHeader
        title="公海回收"
        subtitle="复核本团队客户的回收候选：批准 / 驳回 / 暂缓。看得见哪些候选由你的数据范围决定"
      />
      <SectionCard style={{ flex: '0 0 auto' }}>
        <PoolRecyclePanel />
      </SectionCard>
    </div>
  )
}

export default PoolRecyclePage
