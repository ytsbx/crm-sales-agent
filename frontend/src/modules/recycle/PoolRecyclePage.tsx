import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Modal, Popconfirm, Table, Tabs, Tag, Toast } from '@douyinfe/semi-ui'

import PageHeader from '../../shared/components/PageHeader'
import SectionCard from '../../shared/components/SectionCard'
import { useNotePrompt } from '../../shared/components/NotePromptModal'
import { formatBeijingDateTime } from '../../shared/beijing-time'
import {
  batchDecideRecycleCandidates,
  decideRecycleCandidate,
  listRecycleCandidates,
  restoreRecycleCandidate,
  type RecycleCandidateRow,
  type RecycleStatus,
} from '../../shared/api/settings'

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

/**
 * 公海回收（2026-11-19 从「系统设置 · 业务规则」独立出来）。
 *
 * 原来这一整块（4 个页签 + 批量批准 / 暂缓 / 驳回 + 逐条复核）缩在系统设置里，
 * 主管要进系统设置才能干日常活儿 —— 那是配置页，不是作业页。主人拍板：
 * 复核搬成「销售作业」下一个独立入口（和线索中心、客户中心并列），
 * 系统设置只留下规则配置（天数、保护期、恢复权限、扫描按钮）。
 *
 * 这里只做"看候选 + 做决定"，不改任何规则；规则改动仍在系统设置。
 */
export default function PoolRecyclePage() {
  const notePrompt = useNotePrompt()
  const queryClient = useQueryClient()

  // 1024 下六列排不开（会横向滚动），窄屏把「原负责人 / 最近有效联系 / 保护事项」
  // 三列收起来：复核时主要看客户、最早可回收、以及三个动作。
  const [narrowLayout, setNarrowLayout] = useState(
    () => typeof window !== 'undefined' && window.innerWidth <= 1280,
  )
  useEffect(() => {
    const onResize = () => setNarrowLayout(window.innerWidth <= 1280)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // 四个状态页签：待复核 / 已暂缓 / 已回收 / 已驳回。
  // 只查 pending 的话，"暂缓"一点记录就从页面消失、已回收的也找不回来（返修单第六批第 10 条）。
  const [candidatePage, setCandidatePage] = useState(1)
  const [candidatePageSize, setCandidatePageSize] = useState(20)
  const [candidateStatus, setCandidateStatus] = useState<RecycleStatus>('pending')
  const [selectedCandidateIds, setSelectedCandidateIds] = useState<number[]>([])

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
  }

  const decideMutation = useMutation({
    mutationFn: ({ id, decision, note, early }: {
      id: number
      decision: 'approve' | 'reject' | 'defer'
      note?: string
      early?: boolean
    }) => decideRecycleCandidate(id, decision, note, early),
    onSuccess: (row) => {
      Toast.success(`已${row.status_label}`)
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
      setBatchResult(data)
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
    mutationFn: ({ id, note }: { id: number; note?: string }) => restoreRecycleCandidate(id, note),
    onSuccess: () => {
      Toast.success('客户已恢复给原负责人')
      refreshCandidates()
      void queryClient.invalidateQueries({ queryKey: ['customers'] })
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
    if (!needNote) {
      Modal.confirm({
        title: `确认回收客户「${row.customer_name ?? '客户编号 ' + row.customer_id}」？`,
        content: '执行后客户将进入公海，原负责人将不再负责该客户。',
        okText: '确认回收',
        onOk: () => decideMutation.mutate({ id: row.id, decision }),
      })
      return
    }
    const note = await notePrompt.ask({
      title: `${label}「${row.customer_name ?? `客户 ${row.customer_id}`}」`,
      label: `${label}原因`,
      description: early
        ? `提前回收会让预告 / 暂缓等待期失效（最早可回收时间 ${fmtDay(row.earliest_action_at)}），所以需要你写清原因。`
        : decision === 'approve' && protected_
          ? `该客户仍有履约事项：${(row.protection ?? []).join('；')}。确需例外回收，请说明依据。`
          : undefined,
      placeholder: early
        ? '例如：客户已确认终止合作，无需再等预告期'
        : '例如：客户明确表示需要延长跟进周期',
      required: true,
      tone: early || (decision === 'approve' && protected_) ? 'warning' : 'normal',
      okText: early ? '确认提前回收' : '确认',
    })
    if (note === null) return
    decideMutation.mutate({ id: row.id, decision, note, early })
  }

  /** 单条恢复：把已回收的客户还给原负责人（已被别人领走时会报冲突）。 */
  const restore = async (row: RecycleCandidateRow) => {
    const note = await notePrompt.ask({
      title: `恢复「${row.customer_name ?? `客户 ${row.customer_id}`}」给原负责人`,
      label: '恢复原因',
      description: '客户会从公海回到原负责人名下，其他同事不再能领取。',
      placeholder: '例如：客户回访后确认继续合作',
      required: true,
      okText: '确认恢复',
    })
    if (note === null) return
    restoreMutation.mutate({ id: row.id, note: note || undefined })
  }

  return (
    <div className="page-container">
      {notePrompt.node}
      <PageHeader
        title="公海回收"
        subtitle="复核本团队客户的回收候选：批准 / 驳回 / 暂缓。看得见哪些候选由你的数据范围决定"
      />
      <SectionCard>
        <div className="rule-block" style={{ marginTop: 0 }}>
          <div className="rule-block-title">回收复核</div>
          {/* 这段说明原来写在系统设置里，跟着搬过来：它讲的是"怎么干这个活"，
              不是"规则怎么配"。 */}
          <div
            style={{ color: 'var(--crm-text-2)', fontSize: 12, lineHeight: 1.65, marginBottom: 12 }}
          >
            扫描只「提名」；批准后才真的回收，并且<b>必须等预告期/暂缓期满了</b>才能批
            —— 没满要走「提前回收」这个例外动作，得填原因。
            「驳回」不受等待期限制（表示不用回收了，随时可结案）。
            客户手上还有在途订单、未结应收、有效正式报价或在途打样时，批准会被拦下。
          </div>
          <Tabs
            type="button"
            activeKey={candidateStatus}
            onChange={(key) => {
              setCandidateStatus(key as RecycleStatus)
              setCandidatePage(1)
              setSelectedCandidateIds([])
              setBatchResult(null)
            }}
            tabList={[
              { tab: '待复核', itemKey: 'pending' },
              { tab: '已暂缓', itemKey: 'deferred' },
              { tab: '已回收', itemKey: 'executed' },
              { tab: '已驳回', itemKey: 'rejected' },
            ]}
            style={{ marginBottom: 12 }}
          />
          {(candidateStatus === 'pending' || candidateStatus === 'deferred') && (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                marginBottom: 8,
              }}
            >
              <span style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
                已选 {selectedCandidateIds.length} 条
              </span>
              <div style={{ flex: 1 }} />
              {/* 批量决定一次影响多个人/多个客户，点一下立刻生效太轻
                  （主人 2026-10-06 定的范围：批量与不可逆的动作都要确认）。 */}
              <Popconfirm
                title={`批准选中的 ${selectedCandidateIds.length} 条回收候选？`}
                content="批准后进入待执行；到期仍未恢复的客户会被真正回收（归属清空）。"
                onConfirm={() => batchMutation.mutate({ decision: 'approve' })}
              >
                <Button
                  size="small"
                  disabled={!selectedCandidateIds.length}
                  loading={batchMutation.isPending}
                >
                  批量批准
                </Button>
              </Popconfirm>
              <Popconfirm
                title={`暂缓选中的 ${selectedCandidateIds.length} 条回收候选？`}
                content="暂缓后这些客户这一轮不会被回收，过一阵会重新进入候选。"
                onConfirm={() => batchMutation.mutate({ decision: 'defer' })}
              >
                <Button
                  size="small"
                  disabled={!selectedCandidateIds.length}
                  loading={batchMutation.isPending}
                >
                  批量暂缓
                </Button>
              </Popconfirm>
              <Popconfirm
                title={`驳回选中的 ${selectedCandidateIds.length} 条回收候选？`}
                content="驳回后本轮不再回收这些客户，需要重新提名才会再进候选。"
                onConfirm={() => batchMutation.mutate({ decision: 'reject' })}
              >
                <Button
                  size="small"
                  type="danger"
                  disabled={!selectedCandidateIds.length}
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
                  候选 #{row.candidate_id}：{row.reason}
                </div>
              ))}
            </div>
          )}
          <Table<RecycleCandidateRow>
            rowKey="id"
            className="recycle-cand-table"
            loading={candidatesQuery.isLoading}
            dataSource={candidatesQuery.data?.items ?? []}
            pagination={{
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
            }}
            rowSelection={
              candidateStatus === 'pending' || candidateStatus === 'deferred'
                ? {
                    selectedRowKeys: selectedCandidateIds,
                    onChange: (keys) => setSelectedCandidateIds((keys ?? []) as number[]),
                  }
                : undefined
            }
            empty={
              // 空表格只有一行灰字，看着像"页面坏了"。待复核为空其实是**正常且好**的
              // 状态（说明没客户该被回收），得把这句话说出来，再给个能动的入口。
              <div style={{ padding: '20px 0 10px' }}>
                <div style={{ fontSize: 13, color: 'var(--crm-text-2)' }}>
                  {candidateStatus === 'pending'
                    ? '没有待复核的候选'
                    : `没有${STATUS_LABEL[candidateStatus]}的候选`}
                </div>
                <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginTop: 6 }}>
                  {candidateStatus === 'pending'
                    ? '当前没有客户达到回收条件 —— 这是正常状态。要重新扫描，去「系统设置 · 业务规则」里点「扫描并生成回收预告」。'
                    : '按上面的标签切回「待复核」可以看到现在需要处理的候选。'}
                </div>
              </div>
            }
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
                            title: '保护事项',
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
                                  <span style={{ color: 'var(--crm-caution, #b26a00)' }}>
                                    {(list ?? []).join('；')}
                                  </span>
                                </>
                              ),
                          },
                        ]),
                    {
                      title: '最早可回收',
                      dataIndex: 'earliest_action_at',
                      width: narrowLayout ? 130 : 132,
                      render: (v: string | null, r: RecycleCandidateRow) =>
                        v ? (
                          <div
                            style={{
                              color: isDue(r) ? 'var(--crm-text-2)' : 'var(--crm-caution, #b26a00)',
                              lineHeight: 1.5,
                            }}
                          >
                            <div>{fmtDay(v)}</div>
                            {!isDue(r) && (
                              <div style={{ fontSize: 11 }}>
                                （{r.status === 'deferred' ? '暂缓期' : '预告期'}中）
                              </div>
                            )}
                          </div>
                        ) : (
                          '-'
                        ),
                    },
                  ]),
              {
                title: '操作',
                width: candidateStatus === 'executed' ? 90 : 172,
                render: (_: unknown, r: RecycleCandidateRow) => {
                  if (candidateStatus === 'executed') {
                    return (
                      <span>
                        {r.restored_at ? (
                          <Tag color="green" type="light" size="small">
                            已恢复
                          </Tag>
                        ) : (
                          <a onClick={() => restore(r)}>恢复</a>
                        )}
                      </span>
                    )
                  }
                  if (candidateStatus === 'rejected') {
                    return <span style={{ color: 'var(--crm-text-3)' }}>已结案</span>
                  }
                  return (
                    <span style={{ display: 'inline-flex', gap: 10 }}>
                      {isDue(r) ? (
                        <a onClick={() => decide('approve', r)}>批准回收</a>
                      ) : (
                        <a
                          style={{ color: 'var(--crm-danger, #d45)' }}
                          onClick={() => decide('approve', r, { early: true })}
                        >
                          提前回收
                        </a>
                      )}
                      <a onClick={() => decide('defer', r)}>暂缓</a>
                      <a
                        style={{ color: 'var(--crm-danger, #d45)' }}
                        onClick={() => decide('reject', r)}
                      >
                        驳回
                      </a>
                    </span>
                  )
                },
              },
            ]}
          />
        </div>
      </SectionCard>
    </div>
  )
}
