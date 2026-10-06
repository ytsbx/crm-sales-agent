import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Banner, Button, Empty, Input, Modal, Select, Table, Tag, Toast } from '@douyinfe/semi-ui'

import { listUsers } from '../../shared/api/system'
import KpiStrip from '../../shared/components/KpiStrip'
import PageHeader from '../../shared/components/PageHeader'
import { usePermissions } from '../../shared/hooks/permissions'
import {
  bindUnboundContact,
  createCustomerFromContact,
  getUnboundCandidates,
  getWeComReadiness,
  getWeComTransferPreview,
  ignoreUnboundContact,
  listUnboundContacts,
  listWeComSyncJobs,
  listWeComTransferItems,
  retryWeComTransfer,
  syncWeComDepartments,
  syncWeComExternalContacts,
  syncWeComFollowRelations,
  syncWeComUsers,
  transferWeComRelations,
  type WeComSyncJob,
  type WeComTransferItem,
  type WeComTransferPreview,
  type WeComUnboundContact,
} from '../../shared/api/wecom'
import type { TagTone } from '../../shared/types'
import SectionCard from '../../shared/components/SectionCard'
import FormLabel from '../../shared/components/FormLabel'

/**
 * 企业微信待归一页面（04-UI §5，布局照设计稿：左待处理 / 中企微详情 / 右候选客户）。
 *
 * 这个页面的核心是**人工确认**：PRD §6.3 与总设计文档 §5.2 都明确要求
 * "企微外部联系人不等于 CRM 客户"，所以候选只做排序提示，
 * 三个出口（关联已有 / 创建新客户 / 暂不处理）必须由人点。
 */

const JOB_STATUS_TONE: Record<string, TagTone> = {
  running: 'blue',
  success: 'green',
  partial: 'orange',
  failed: 'red',
}

const SYNC_BUTTONS = [
  { key: 'departments', label: '同步部门', run: syncWeComDepartments },
  { key: 'users', label: '同步成员', run: syncWeComUsers },
  { key: 'external', label: '同步外部联系人', run: syncWeComExternalContacts },
  { key: 'follow', label: '同步跟进关系', run: syncWeComFollowRelations },
]

export default function WeComPage() {
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const canManage = can('wecom:manage')

  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)
  const [activeId, setActiveId] = useState<number | null>(null)

  const [createVisible, setCreateVisible] = useState(false)
  const [createForm, setCreateForm] = useState({ name: '', region: '', level: '' })
  const [transferVisible, setTransferVisible] = useState(false)
  const [transferForm, setTransferForm] = useState<{
    handover_user_id?: number
    takeover_user_id?: number
  }>({})
  //: 交接走三步：填人 → **先看清单** → 执行后看逐项结果。
  //: 中间那步是必须的：合并/交接这类不可逆动作，不能点一下才发现
  //: 有两百个对象跟着换人。
  const [transferStage, setTransferStage] = useState<'form' | 'preview' | 'result'>('form')
  const [transferPreview, setTransferPreview] = useState<WeComTransferPreview | null>(null)
  //: 逐项调整过的接手人（键 `kind:business_id`）。业务方 2026-10-06 定：
  //: "交接清单可以逐项调整" —— 某个客户本来就该归另一位同事时不用再操作一遍。
  const [transferAssignees, setTransferAssignees] = useState<Record<string, number>>({})
  const [transferJob, setTransferJob] = useState<WeComSyncJob | null>(null)
  //: 交接后**还没办完**的项（失败或没轮到的），可以逐条重试
  const [transferOpen, setTransferOpen] = useState<WeComTransferItem[]>([])

  const readinessQuery = useQuery({
    queryKey: ['wecom-readiness'],
    queryFn: getWeComReadiness,
  })

  const listQuery = useQuery({
    queryKey: ['wecom-unbound', { keyword, page }],
    queryFn: () => listUnboundContacts({ keyword, page, page_size: 20 }),
  })

  const rows = listQuery.data?.items ?? []
  // 默认选中第一条；列表变了以后如果当前选中项已不在列表里，回落到第一条
  useEffect(() => {
    if (rows.length === 0) {
      setActiveId(null)
      return
    }
    if (!rows.some((row) => row.id === activeId)) {
      setActiveId(rows[0].id)
    }
  }, [rows, activeId])

  const active: WeComUnboundContact | undefined = rows.find((row) => row.id === activeId)
  const candidatesQuery = useQuery({
    queryKey: ['wecom-candidates', activeId],
    queryFn: () => getUnboundCandidates(activeId!),
    enabled: Boolean(activeId),
  })

  const jobsQuery = useQuery({
    queryKey: ['wecom-jobs'],
    queryFn: () => listWeComSyncJobs({ page: 1, page_size: 10 }),
  })

  const usersQuery = useQuery({
    queryKey: ['users-for-select'],
    queryFn: () => listUsers({ page: 1, page_size: 100 }),
    enabled: transferVisible,
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['wecom-unbound'] })
    void queryClient.invalidateQueries({ queryKey: ['wecom-candidates'] })
    void queryClient.invalidateQueries({ queryKey: ['wecom-jobs'] })
    void queryClient.invalidateQueries({ queryKey: ['wecom-readiness'] })
    void queryClient.invalidateQueries({ queryKey: ['customers'] })
  }

  const syncMutation = useMutation({
    mutationFn: (run: () => Promise<unknown>) => run(),
    onSuccess: () => {
      Toast.success('同步完成')
      // 同步结果里带"哪些人 CRM 没有账号"，直接说清楚，别只报成功条数
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const bindMutation = useMutation({
    mutationFn: (customerId: number) => bindUnboundContact(activeId!, { customer_id: customerId }),
    onSuccess: (result) => {
      Toast.success(`已关联到客户「${result.customer_name}」`)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const createMutation = useMutation({
    mutationFn: () =>
      createCustomerFromContact(activeId!, {
        name: createForm.name.trim(),
        region: createForm.region || null,
        level: createForm.level || null,
      }),
    onSuccess: (result) => {
      Toast.success(`已创建客户「${result.customer_name}」`)
      setCreateVisible(false)
      setCreateForm({ name: '', region: '', level: '' })
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const ignoreMutation = useMutation({
    mutationFn: () => ignoreUnboundContact(activeId!, '界面标记暂不处理'),
    onSuccess: () => {
      Toast.success('已标记为暂不处理')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const resetTransfer = () => {
    setTransferVisible(false)
    setTransferStage('form')
    setTransferPreview(null)
    setTransferJob(null)
    setTransferOpen([])
    setTransferAssignees({})
    setTransferForm({})
  }

  /** 拉这次交接里**还没办完**的项（失败或没轮到的）。 */
  const loadOpenItems = async (jobId: number) => {
    const result = await listWeComTransferItems(jobId, { pending_only: true, page_size: 100 })
    setTransferOpen(result.items)
    return result.total
  }

  const previewMutation = useMutation({
    mutationFn: () =>
      getWeComTransferPreview(transferForm.handover_user_id!, transferForm.takeover_user_id!),
    onSuccess: (preview) => {
      setTransferPreview(preview)
      setTransferStage('preview')
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const transferMutation = useMutation({
    mutationFn: () =>
      transferWeComRelations({
        handover_user_id: transferForm.handover_user_id!,
        takeover_user_id: transferForm.takeover_user_id!,
        // 没配外部联系人 secret 时只转 CRM 侧，避免调企微报错白跑一趟
        transfer_wecom: Boolean(readinessQuery.data?.can_sync_external),
        item_assignees: Object.keys(transferAssignees).length ? transferAssignees : undefined,
      }),
    onSuccess: async (job) => {
      const detail = (job.detail ?? {}) as Record<string, unknown>
      const failed = Number(detail.failures_count ?? 0)
      const frozen = Number(detail.frozen ?? 0)
      // **按实际结果说人话**：不再笼统报"继承完成"。
      // 部分成功时要说清成功了多少、还剩多少、下一步去哪处理。
      if (failed || frozen) {
        const parts: string[] = []
        if (frozen) parts.push(`${frozen} 个客户因撞单争议被冻结`)
        if (failed) parts.push(`${failed} 项没成`)
        Toast.warning(`交接部分完成：${parts.join('、')}。可在下方逐项处理`)
      } else {
        Toast.success('交接完成')
      }
      setTransferJob(job)
      setTransferStage('result')
      const remaining = await loadOpenItems(job.id)
      refresh()
      void queryClient.invalidateQueries({ queryKey: ['customers'] })
      void queryClient.invalidateQueries({ queryKey: ['opportunities'] })
      return remaining
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const retryMutation = useMutation({
    mutationFn: () => retryWeComTransfer(transferJob!.id),
    onSuccess: async (result) => {
      Toast.success(`已重试 ${result.retried} 项，还剩 ${result.remaining} 项未完成`)
      await loadOpenItems(transferJob!.id)
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const readiness = readinessQuery.data
  const configured = readiness?.configured
  const missing = configured
    ? Object.entries(configured)
        .filter(([, value]) => !value)
        .map(([key]) => key)
    : []

  return (
    <div className="page-container">
      <PageHeader
        title="企业微信"
        subtitle="外部联系人同步、待归一确认、离职继承"
        extra={
          canManage && (
            <>
              <Button onClick={() => setTransferVisible(true)}>离职继承</Button>
              {SYNC_BUTTONS.map((item) => (
                <Button
                  key={item.key}
                  loading={syncMutation.isPending}
                  onClick={() => syncMutation.mutate(item.run)}
                >
                  {item.label}
                </Button>
              ))}
            </>
          )
        }
      />

      {missing.length > 0 && (
        <Banner
          type="warning"
          closeIcon={null}
          style={{ marginBottom: 12 }}
          description={
            <span>
              企业微信还没配置齐全（缺：{missing.join('、')}）。同步接口会明确报错而不是假装成功；
              请在 <code>backend/.env</code> 里补 <code>WECOM_CORP_ID</code> 等配置后重启后端。
              未配置期间，「待归一」的数据为空是正常的，页面与接口本身可以直接联调。
            </span>
          }
        />
      )}

      <KpiStrip
        items={[
          {
            label: '企微成员',
            value: readiness?.counts.wecom_users ?? '-',
            hint: '含未建 CRM 账号的成员',
          },
          {
            label: '外部联系人',
            value: readiness?.counts.external_contacts ?? '-',
            hint: '企微侧同步到的总数',
          },
          { label: '跟进关系', value: readiness?.counts.follow_relations ?? '-', hint: '谁加了谁' },
          { label: '待归一', value: readiness?.counts.unbound_contacts ?? '-', hint: '需要人工确认' },
          {
            label: '未建账号',
            value: readiness?.counts.unmatched_users ?? '-',
            hint: '企微有、CRM 没有',
          },
        ]}
      />

      {/* 三栏原来写死 300px + 320px，窗口一窄就先把自己撑破（整页横向滚动） */}
      <div className="split-multi" style={{ gap: 16 }}>
        {/* 左：待处理外部联系人 */}
        <SectionCard>
          <div style={{ fontWeight: 600, marginBottom: 10 }}>待处理联系人</div>
          <Input
            placeholder="搜索昵称或企业名"
            value={keywordInput}
            onChange={setKeywordInput}
            onEnterPress={() => {
              setKeyword(keywordInput.trim())
              setPage(1)
            }}
            showClear
            style={{ marginBottom: 10 }}
          />
          {rows.length === 0 ? (
            <Empty description={listQuery.isLoading ? '加载中…' : '没有待归一联系人'} />
          ) : (
            <div style={{ display: 'grid', gap: 6 }}>
              {rows.map((row) => (
                <div
                  key={row.id}
                  onClick={() => setActiveId(row.id)}
                  style={{
                    padding: '8px 10px',
                    borderRadius: 'var(--crm-radius-sm)',
                    cursor: 'pointer',
                    background: row.id === activeId ? 'var(--crm-primary-soft)' : 'transparent',
                    border: `1px solid ${row.id === activeId ? 'var(--crm-primary)' : 'transparent'}`,
                  }}
                >
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{row.name ?? '未命名'}</div>
                  <div
                    style={{
                      fontSize: 12,
                      color: 'var(--crm-text-3)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {row.corp_name ?? '个人微信'}
                  </div>
                </div>
              ))}
            </div>
          )}
          <div style={{ marginTop: 10, textAlign: 'right' }}>
            <Button
              size="small"
              disabled={page <= 1}
              onClick={() => setPage((current) => Math.max(1, current - 1))}
            >
              上一页
            </Button>
            <Button
              size="small"
              style={{ marginLeft: 6 }}
              disabled={rows.length < 20}
              onClick={() => setPage((current) => current + 1)}
            >
              下一页
            </Button>
          </div>
        </SectionCard>

        {/* 中：企业微信详情 */}
        <SectionCard>
          {!active ? (
            <Empty description="左侧选一条待归一联系人" />
          ) : (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
                <div
                  style={{
                    width: 44,
                    height: 44,
                    borderRadius: '50%',
                    background: 'var(--crm-surface-low)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    overflow: 'hidden',
                    flex: '0 0 44px',
                  }}
                >
                  {active.avatar ? (
                    <img src={active.avatar} alt="" style={{ width: '100%', height: '100%' }} />
                  ) : (
                    (active.name ?? '?').slice(0, 1)
                  )}
                </div>
                <div>
                  <div style={{ fontSize: 16, fontWeight: 600 }}>{active.name ?? '未命名'}</div>
                  <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                    {active.corp_name ?? '个人微信'}
                  </div>
                </div>
              </div>

              <div style={{ display: 'grid', gap: 8, fontSize: 13 }}>
                <div style={{ display: 'flex', gap: 8 }}>
                  <span style={{ color: 'var(--crm-text-3)', minWidth: 84 }}>企业微信来源</span>
                  <span>{active.type === '2' ? '企业微信用户' : '微信用户'}</span>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <span style={{ color: 'var(--crm-text-3)', minWidth: 84 }}>external_userid</span>
                  <span style={{ wordBreak: 'break-all' }}>{active.external_userid}</span>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <span style={{ color: 'var(--crm-text-3)', minWidth: 84 }}>同步时间</span>
                  <span>
                    {active.last_sync_at
                      ? new Date(active.last_sync_at).toLocaleString('zh-CN')
                      : '-'}
                  </span>
                </div>
              </div>

              <div style={{ marginTop: 14 }}>
                <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>跟进员工</div>
                {active.followers.length === 0 ? (
                  <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>
                    还没有跟进关系（先点上方「同步跟进关系」）
                  </div>
                ) : (
                  <div style={{ display: 'grid', gap: 6 }}>
                    {active.followers.map((follower) => (
                      <div
                        key={follower.wecom_userid}
                        style={{ display: 'flex', gap: 8, fontSize: 13, alignItems: 'center' }}
                      >
                        <Tag size="small" color={follower.status === 'active' ? 'blue' : 'grey'}>
                          {follower.status === 'active' ? '跟进中' : follower.status}
                        </Tag>
                        <span>{follower.wecom_userid}</span>
                        <span style={{ color: 'var(--crm-text-3)', marginLeft: 'auto' }}>
                          {follower.add_time
                            ? new Date(follower.add_time).toLocaleDateString('zh-CN')
                            : '-'}
                          {follower.add_way ? ` · ${follower.add_way}` : ''}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </SectionCard>

        {/* 右：系统候选客户 */}
        <SectionCard>
          <div style={{ fontWeight: 600, marginBottom: 10 }}>疑似客户</div>
          {!active ? (
            <Empty description="选中联系人后显示候选" />
          ) : (
            <>
              {candidatesQuery.isLoading && (
                <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>匹配中…</div>
              )}
              {(candidatesQuery.data?.candidates ?? []).length === 0 && !candidatesQuery.isLoading && (
                <div style={{ color: 'var(--crm-text-3)', fontSize: 13, marginBottom: 10 }}>
                  没有匹配到疑似客户，可以直接创建新客户
                </div>
              )}
              <div style={{ display: 'grid', gap: 8 }}>
                {(candidatesQuery.data?.candidates ?? []).map((candidate, index) => (
                  <div
                    key={candidate.id}
                    style={{
                      padding: 10,
                      borderRadius: 'var(--crm-radius-sm)',
                      border: '1px solid var(--crm-surface-high)',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                        {index + 1}
                      </span>
                      <span style={{ fontWeight: 600, fontSize: 13 }}>{candidate.name}</span>
                      <Tag size="small" color={candidate.score >= 80 ? 'green' : 'orange'}>
                        {candidate.score}%
                      </Tag>
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginTop: 4 }}>
                      {candidate.reasons.slice(0, 3).join('；')}
                    </div>
                    {canManage && (
                      <Button
                        size="small"
                        theme="solid"
                        style={{ marginTop: 8 }}
                        loading={bindMutation.isPending}
                        onClick={() => bindMutation.mutate(candidate.id)}
                      >
                        关联已有客户
                      </Button>
                    )}
                  </div>
                ))}
              </div>

              {canManage && active && (
                <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>
                  <Button
                    onClick={() => {
                      setCreateForm({
                        name: active.corp_name || active.name || '',
                        region: '',
                        level: '',
                      })
                      setCreateVisible(true)
                    }}
                  >
                    创建新客户
                  </Button>
                  <Button
                    loading={ignoreMutation.isPending}
                    onClick={() => ignoreMutation.mutate()}
                  >
                    暂不处理
                  </Button>
                </div>
              )}
            </>
          )}
        </SectionCard>
      </div>

      <SectionCard style={{ marginTop: 16 }}>
        <div style={{ fontWeight: 600, marginBottom: 12 }}>同步任务</div>
        <Table
          columns={[
            { title: '类型', dataIndex: 'job_type_label', width: 130 },
            {
              title: '状态',
              dataIndex: 'status',
              width: 100,
              render: (value: string, record: { status_label: string }) => (
                <Tag color={JOB_STATUS_TONE[value] ?? 'grey'} size="small">
                  {record.status_label}
                </Tag>
              ),
            },
            { title: '成功', dataIndex: 'success_count', width: 80 },
            { title: '失败', dataIndex: 'fail_count', width: 80 },
            {
              title: '开始时间',
              dataIndex: 'started_at',
              width: 180,
              render: (value: string) => new Date(value).toLocaleString('zh-CN'),
            },
            {
              title: '错误信息',
              dataIndex: 'error_message',
              render: (value: string | null) => value ?? '-',
            },
          ]}
          dataSource={jobsQuery.data?.items ?? []}
          rowKey="id"
          size="small"
          pagination={false}
          loading={jobsQuery.isLoading}
          empty="还没有同步记录"
        />
      </SectionCard>

      <Modal
        title="创建新客户"
        visible={createVisible}
        onCancel={() => setCreateVisible(false)}
        onOk={() => {
          if (!createForm.name.trim()) {
            Toast.warning('客户名称必填')
            return
          }
          createMutation.mutate()
        }}
        confirmLoading={createMutation.isPending}
        okText="创建并关联"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <FormLabel required>客户名称</FormLabel>
            <Input
              value={createForm.name}
              onChange={(value) => setCreateForm({ ...createForm, name: value })}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>地区</div>
            <Input
              value={createForm.region}
              onChange={(value) => setCreateForm({ ...createForm, region: value })}
              placeholder="例如：华东"
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>客户等级</div>
            <Select
              value={createForm.level || undefined}
              onChange={(value) => setCreateForm({ ...createForm, level: value as string })}
              optionList={['A', 'B', 'C', 'D'].map((value) => ({ value, label: `${value} 级` }))}
              showClear
              style={{ width: '100%' }}
            />
          </div>
          <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
            创建后这条企微联系人会作为该客户的联系人挂上，来源记为「企业微信」。
          </div>
        </div>
      </Modal>

      <Modal
        title="离职继承"
        visible={transferVisible}
        onCancel={resetTransfer}
        footer={
          transferStage === 'form' ? (
            <>
              <Button onClick={resetTransfer}>取消</Button>
              <Button
                theme="solid"
                loading={previewMutation.isPending}
                onClick={() => {
                  if (!transferForm.handover_user_id || !transferForm.takeover_user_id) {
                    Toast.warning('请选择交接人与接管人')
                    return
                  }
                  previewMutation.mutate()
                }}
              >
                查看交接清单
              </Button>
            </>
          ) : transferStage === 'preview' ? (
            <>
              <Button onClick={() => setTransferStage('form')}>返回修改</Button>
              <Button
                theme="solid"
                loading={transferMutation.isPending}
                onClick={() => transferMutation.mutate()}
              >
                执行交接
              </Button>
            </>
          ) : (
            <>
              <Button onClick={resetTransfer}>关闭</Button>
              {transferOpen.length > 0 && (
                <Button
                  theme="solid"
                  loading={retryMutation.isPending}
                  onClick={() => retryMutation.mutate()}
                >
                  重试未完成的 {transferOpen.length} 项
                </Button>
              )}
            </>
          )
        }
        width={680}
      >
        {transferStage === 'form' && (
          <div style={{ display: 'grid', gap: 12 }}>
            <div>
              <FormLabel required>交接人（离职）</FormLabel>
              <Select
                value={transferForm.handover_user_id}
                onChange={(value) =>
                  setTransferForm({ ...transferForm, handover_user_id: value as number })
                }
                optionList={(usersQuery.data?.items ?? []).map((user) => ({
                  value: user.id,
                  label: `${user.name}（${user.username}）`,
                }))}
                filter
                style={{ width: '100%' }}
              />
            </div>
            <div>
              <FormLabel required>接管人</FormLabel>
              <Select
                value={transferForm.takeover_user_id}
                onChange={(value) =>
                  setTransferForm({ ...transferForm, takeover_user_id: value as number })
                }
                optionList={(usersQuery.data?.items ?? []).map((user) => ({
                  value: user.id,
                  label: `${user.name}（${user.username}）`,
                }))}
                filter
                style={{ width: '100%' }}
              />
            </div>
            <div style={{ color: 'var(--crm-text-2)', fontSize: 12.5 }}>
              会转移：企微客户关系
              {readiness?.can_sync_external ? '' : '（未配外部联系人密钥，本次跳过企微侧，只转 CRM）'}
              、CRM 客户负责人、商机负责人、未完成任务、在途打样单、订单与草稿。
              <br />
              会保留：创建人、历史跟进、历史报价、审批与审计日志、签单业绩归属；
              客户名下其他在职同事的待办也不动。
              <br />
              下一步会先给你看完整交接清单（每一项都可以单独改接手人），确认之后再执行。
            </div>
          </div>
        )}

        {transferStage === 'preview' && transferPreview && (
          <div style={{ display: 'grid', gap: 10 }}>
            <div style={{ fontSize: 13.5 }}>
              把「{transferPreview.handover.name}」名下的 <b>{transferPreview.total}</b> 项
              交接给「{transferPreview.takeover.name}」
            </div>
            {transferPreview.sample_count > 0 && (
              <Banner
                type="info"
                closeIcon={null}
                description={
                  <span>
                    含 <b>{transferPreview.sample_count}</b> 张在途打样单。
                    交过去之后接手人才看得到、能继续跟；原负责人将看不到。
                  </span>
                }
              />
            )}
            <div style={{ maxHeight: 320, overflow: 'auto', display: 'grid', gap: 8 }}>
              {transferPreview.sections.map((section) => (
                <div
                  key={section.kind}
                  style={{
                    border: '1px solid var(--crm-border)',
                    borderRadius: 6,
                    padding: '8px 10px',
                  }}
                >
                  <div style={{ fontWeight: 600, fontSize: 13 }}>
                    {section.label}（{section.items.length}）
                  </div>
                  <div style={{ fontSize: 12.5, color: 'var(--crm-text-2)', marginTop: 4 }}>
                    {section.items.map((item) => {
                      const key = `${item.kind}:${item.business_id}`
                      return (
                        <div
                          key={key}
                          style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}
                        >
                          <span style={{ flex: 1 }}>
                            · {item.label}
                            {item.blocked_reason ? (
                              <Tag color="orange" size="small" style={{ marginLeft: 6 }}>
                                本次冻结
                              </Tag>
                            ) : null}
                          </span>
                          {/* 逐项调整接手人：不选就是跟统一接管人 */}
                          {!item.blocked_reason && (
                            <Select
                              size="small"
                              value={transferAssignees[key]}
                              placeholder={`跟随 ${transferPreview.takeover.name}`}
                              showClear
                              filter
                              onChange={(value) =>
                                setTransferAssignees((prev) => {
                                  const next = { ...prev }
                                  if (value === undefined || value === null) delete next[key]
                                  else next[key] = value as number
                                  return next
                                })
                              }
                              optionList={(usersQuery.data?.items ?? [])
                                .filter((user) => user.id !== transferForm.handover_user_id)
                                .map((user) => ({ value: user.id, label: user.name }))}
                              style={{ width: 168 }}
                            />
                          )}
                        </div>
                      )
                    })}
                  </div>
                </div>
              ))}
            </div>
            {transferPreview.blocked.length > 0 && (
              <Banner
                type="warning"
                closeIcon={null}
                description={
                  <span>
                    有 {transferPreview.blocked.length} 个客户处于<b>撞单争议</b>中：
                    归属冻结、企微转接也不会发出，等主管裁定后单独发起一次交接。
                  </span>
                }
              />
            )}
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
              {transferPreview.note}
            </div>
          </div>
        )}

        {transferStage === 'result' && transferJob && (
          <div style={{ display: 'grid', gap: 10 }}>
            <div style={{ fontSize: 13.5 }}>
              任务 #{transferJob.id}：<b>{transferJob.status_label}</b>
              ，成功 {transferJob.success_count} 项、失败 {transferJob.fail_count} 项
            </div>
            {transferOpen.length === 0 ? (
              <Empty description="没有未完成的项" />
            ) : (
              <>
                <div style={{ fontSize: 12.5, color: 'var(--crm-text-2)' }}>
                  下面这 {transferOpen.length} 项还没完成。可以点右下角重试 ——
                  已经转出去的企微关系不会再发一遍。
                </div>
                <Table<WeComTransferItem>
                  rowKey="id"
                  size="small"
                  pagination={false}
                  dataSource={transferOpen}
                  columns={[
                    { title: '类别', dataIndex: 'kind_label', width: 80 },
                    {
                      title: '对象',
                      dataIndex: 'label',
                      render: (v: string | null) => v || '—',
                    },
                    {
                      title: 'CRM 侧',
                      dataIndex: 'crm_status_label',
                      width: 110,
                      render: (v: string, row) => (
                        <span title={row.crm_error ?? ''}>
                          <Tag color={row.crm_status === 'failed' ? 'red' : 'grey'} size="small">
                            {v}
                          </Tag>
                        </span>
                      ),
                    },
                    {
                      title: '企微侧',
                      dataIndex: 'wecom_status_label',
                      width: 110,
                      render: (v: string, row) => (
                        <span title={row.wecom_error ?? ''}>
                          <Tag
                            color={row.wecom_status === 'failed' ? 'red' : 'grey'}
                            size="small"
                          >
                            {v}
                          </Tag>
                        </span>
                      ),
                    },
                  ]}
                />
                <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                  鼠标停在状态上可以看到具体失败原因。
                </div>
              </>
            )}
          </div>
        )}
      </Modal>
    </div>
  )
}
