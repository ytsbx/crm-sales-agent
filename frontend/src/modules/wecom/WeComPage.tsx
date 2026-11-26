import { formatBeijingTime, formatBeijingDate } from '../../shared/beijing-time'
import { humanizeBackendText } from '../../shared/humanize-text'
import { useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Banner, Button, Dropdown, Empty, Input, Modal, Popconfirm, Select, Table, Tag, Toast } from '@douyinfe/semi-ui'

import KpiStrip from '../../shared/components/KpiStrip'
import PageHeader from '../../shared/components/PageHeader'
import { usePermissions } from '../../shared/hooks/permissions'
import { listDictionaries } from '../../shared/api/settings'
import { listCustomers } from '../../shared/api/customer'
import {
  bindUnboundContact,
  previewWeComCustomerImport,
  importWeComCustomers,
  type WeComCustomerImportPreview,
  createCustomerFromContact,
  getUnboundCandidates,
  getWeComReadiness,
  getWeComTransferPreview,
  ignoreUnboundContact,
  listUnboundContacts,
  listWeComSyncJobs,
  listWeComTransferItems,
  listWeComTransferPeople,
  retryWeComTransfer,
  syncWeComDepartments,
  syncWeComExternalContacts,
  syncWeComFollowRelations,
  syncWeComUsers,
  transferWeComRelations,
  type WeComSyncJob,
  type WeComTransferItem,
  type WeComTransferPeopleSelection,
  type WeComTransferPreview,
  type WeComUnboundContact,
} from '../../shared/api/wecom'
import { ApiError } from '../../shared/api/client'
import GuardedButton from '../../shared/components/GuardedButton'
import type { Customer, TagTone } from '../../shared/types'
import SectionCard from '../../shared/components/SectionCard'
import FormLabel from '../../shared/components/FormLabel'
import HelpTip from '../../shared/components/HelpTip'
import { optionMatcher } from '../../shared/components/optionMatch'

/**
 * 企业微信待确认客户页面（04-UI §5，布局照设计稿：左待处理 / 中企微详情 / 右候选客户）。
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

const TRANSFER_RESULT_PAGE_SIZE = 10

function TransferResultItem({ item, reviewLink }: { item: WeComTransferItem; reviewLink?: React.ReactNode }) {
  return (
    <li className="wecom-transfer-result-item">
      <div className="wecom-transfer-result-heading">
        <strong>{item.label || '未命名事项'}</strong>
        <span>{item.kind_label}{item.to_owner_name && ` · 拟接手人 ${item.to_owner_name}`}</span>
      </div>
      {item.crm_status !== 'not_applicable' && (
        <div className="wecom-transfer-result-status">
          <span>CRM</span>
          <Tag size="small" color={item.crm_status === 'moved' ? 'green' : JOB_STATUS_TONE[item.crm_status] ?? (item.crm_status === 'frozen' || item.crm_taken ? 'orange' : 'grey')}>
            {item.crm_taken ? '负责人已变更' : item.crm_status_label}
          </Tag>
          {item.crm_error && <span>{humanizeBackendText(item.crm_error)}</span>}
          {item.crm_status === 'frozen' && <span>请先完成撞单裁定，再发起一次交接。</span>}
          {reviewLink}
        </div>
      )}
      {item.wecom_status !== 'not_applicable' && (
        <div className="wecom-transfer-result-status">
          <span>企业微信</span>
          <Tag size="small" color={item.wecom_status === 'transferred' ? 'green' : JOB_STATUS_TONE[item.wecom_status] ?? 'grey'}>
            {item.wecom_status_label}
          </Tag>
          {item.wecom_error && <span>{isWeComIpDenied(item.wecom_error) ? WECOM_IP_DENIED_NOTICE : humanizeBackendText(item.wecom_error)}</span>}
        </div>
      )}
    </li>
  )
}

const SYNC_ACTIONS = {
  departments: { label: '同步部门', description: '更新 CRM 部门及上下级关系', run: syncWeComDepartments, scope: 'contact' },
  users: { label: '同步成员', description: '把企微员工与已有 CRM 账号对应起来', run: syncWeComUsers, scope: 'contact' },
  external: { label: '同步外部联系人', description: '拉取客户的昵称和企业名称，供建档前核对', run: syncWeComExternalContacts, scope: 'external' },
  follow: { label: '同步跟进关系', description: '查清每位联系人的企微跟进员工', run: syncWeComFollowRelations, scope: 'external' },
} as const

const WECOM_IP_DENIED_NOTICE = '企业微信拒绝了当前服务器的连接，请联系管理员检查企微可信 IP 设置。'

function isWeComIpDenied(message?: string | null) {
  return Boolean(message && /错误\s*60020\b|not allow to access from your ip/i.test(message))
}

const IMPORT_REVIEW_GUIDANCE: Record<string, string> = {
  '负责人未唯一匹配，需绑定或分配': '企微里没有跟进员工，或跟进员工没开通 CRM 账号、对应了多个不同账号，系统无法决定客户归谁。请用「关联已有客户」沿用原负责人，或用「创建新客户」逐条确认（新建客户归当前操作人）。',
  '只有公司简称，需确认公司全称': '在待确认联系人里按简称查找，核实全称后再关联已有客户或新建档案。',
  '联系人姓名为空或超长': '核对联系人姓名，修正企微资料后重新同步。',
  '客户名称超长': '核对企微公司全称，修正资料后重新同步。',
  '已有同来源联系人，请核对关联': '到客户中心核对已有联系人和所属客户。',
  '存在同名或不可用客户，需人工确认': '先到客户中心核对同名档案及其状态，再决定关联哪家客户。',
  '已有客户负责人不同，需人工确认': '与原跟进员工和现任负责人核对后，再逐条处理。',
}

export default function WeComPage() {
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const customerLevelsQuery = useQuery({
    queryKey: ['dictionaries', 'customer_level'],
    queryFn: () => listDictionaries('customer_level'),
  })
  const canManage = can('wecom:manage')
  const canViewCustomers = can('customer:view')
  const canBindCustomers = canManage && can('customer:update')
  const canCreateCustomers = canManage && can('customer:create')
  const canSearchCustomers = canBindCustomers && canViewCustomers
  const canSyncDirectory = can('wecom:sync_directory')
  const canSyncContacts = can('wecom:sync_contacts')
  const canSync = canSyncDirectory || canSyncContacts
  const canExecuteTransfer = can('wecom:transfer')
  const canPreviewTransfer = canExecuteTransfer || can('wecom:transfer_preview')
  const canViewDisputes = can('customer:view')
  const location = useLocation()
  const transferToResume = location.state?.resumeWeComTransfer as Partial<WeComTransferPeopleSelection> | undefined
  const canPreviewImport = can('wecom:import_preview') || can('wecom:import')
  const canImport = can('wecom:import') && can('customer:create') && can('customer:assign')

  const [importPreview, setImportPreview] = useState<WeComCustomerImportPreview | null>(null)
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)
  const [activeId, setActiveId] = useState<number | null>(null)
  const [manualBindContact, setManualBindContact] = useState<WeComUnboundContact | null>(null)
  const [customerSearch, setCustomerSearch] = useState('')
  const [customerKeyword, setCustomerKeyword] = useState('')
  const [bindTarget, setBindTarget] = useState<Customer | null>(null)

  const [createContact, setCreateContact] = useState<WeComUnboundContact | null>(null)
  const [createdCustomer, setCreatedCustomer] = useState<{ customer_id: number; customer_name: string } | null>(null)
  const [createForm, setCreateForm] = useState({ name: '', region: '', level: '' })
  const [transferVisible, setTransferVisible] = useState(Boolean(transferToResume))
  const [transferResultPage, setTransferResultPage] = useState(1)
  const [transferResultFilter, setTransferResultFilter] = useState<'all' | 'pending' | 'frozen'>('all')
  const [jobsPage, setJobsPage] = useState(1)
  const [transferForm, setTransferForm] = useState<Partial<WeComTransferPeopleSelection>>(transferToResume ?? {})
  //: 交接走三步：填人 → **先看清单** → 执行后看逐项结果。
  //: 中间那步是必须的：合并/交接这类不可逆动作，不能点一下才发现
  //: 有两百个对象跟着换人。
  const [transferStage, setTransferStage] = useState<'form' | 'preview' | 'result'>('form')
  const [transferPreview, setTransferPreview] = useState<WeComTransferPreview | null>(null)
  //: 逐项调整过的接手人（键 `kind:business_id`）。业务方 2026-10-06 定：
  //: "交接清单可以逐项调整" —— 某个客户本来就该归另一位同事时不用再操作一遍。
  const [transferAssignees, setTransferAssignees] = useState<Record<string, number>>({})
  const [transferJob, setTransferJob] = useState<WeComSyncJob | null>(null)
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
    enabled: canViewCustomers && Boolean(activeId),
  })
  const matchingCustomersQuery = useQuery({
    queryKey: ['wecom-customer-search', customerKeyword],
    queryFn: () => listCustomers({ keyword: customerKeyword, status: 'active', page: 1, page_size: 20 }),
    enabled: canSearchCustomers && Boolean(manualBindContact && customerKeyword),
  })
  const searchForCustomer = () => {
    const term = customerSearch.trim()
    if (!term) return
    setBindTarget(null)
    if (term === customerKeyword) void matchingCustomersQuery.refetch()
    else setCustomerKeyword(term)
  }

  const jobsQuery = useQuery({
    queryKey: ['wecom-jobs', jobsPage],
    queryFn: () => listWeComSyncJobs({ page: jobsPage, page_size: 10 }),
  })

  const followJobQuery = useQuery({
    queryKey: ['wecom-jobs', 'last-follow-sync'],
    queryFn: () => listWeComSyncJobs({ job_type: 'follow_relation', page_size: 1 }),
    // 跟进关系同步已改为后台任务：进行中每 3 秒刷新一次，完成后停止
    refetchInterval: (query) => query.state.data?.items?.[0]?.status === 'running' ? 3000 : false,
    refetchIntervalInBackground: true,
  })

  const usersQuery = useQuery({
    queryKey: ['wecom-transfer-people'],
    queryFn: listWeComTransferPeople,
    enabled: transferVisible && canPreviewTransfer,
  })

  const transferPeople = usersQuery.data ?? []
  const selectedHandover = transferPeople.some((person) => person.id === transferForm.handover_user_id)
  const takeoverPeople = transferPeople.filter((person) =>
    person.status === 'active' && person.id !== transferForm.handover_user_id,
  )
  const transferResultQueryKey = ['wecom-transfer-result', transferJob?.id]
  const transferResultEnabled = transferVisible && transferStage === 'result' && Boolean(transferJob)
  const transferItemsQuery = useQuery({
    queryKey: [...transferResultQueryKey, 'items', transferResultPage, transferResultFilter],
    queryFn: () => listWeComTransferItems(transferJob!.id, {
      page: transferResultPage,
      page_size: TRANSFER_RESULT_PAGE_SIZE,
      pending_only: transferResultFilter === 'pending' || undefined,
      crm_status: transferResultFilter === 'frozen' ? 'frozen' : undefined,
    }),
    enabled: transferResultEnabled,
  })
  const transferPendingQuery = useQuery({
    queryKey: [...transferResultQueryKey, 'pending'],
    queryFn: () => listWeComTransferItems(transferJob!.id, { pending_only: true, page_size: 1 }),
    enabled: transferResultEnabled,
  })
  const transferOpenTotal = transferPendingQuery.data?.total ?? 0
  const transferItemTotal = transferItemsQuery.data?.total ?? 0
  const transferMode = readinessQuery.data?.can_sync_external ? 'CRM 和企微' : '仅 CRM'

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['wecom-unbound'] })
    void queryClient.invalidateQueries({ queryKey: ['wecom-candidates'] })
    void queryClient.invalidateQueries({ queryKey: ['wecom-jobs'] })
    void queryClient.invalidateQueries({ queryKey: ['wecom-readiness'] })
    void queryClient.invalidateQueries({ queryKey: ['customers'] })
  }

  const syncMutation = useMutation({
    mutationFn: (run: () => Promise<WeComSyncJob>) => run(),
    onSuccess: (job) => {
      if (job.status === 'running') {
        Toast.info('跟进关系同步已开始，联系人较多时需要几分钟，完成后会自动刷新')
        refresh()
        return
      }
      const conflicts = job.detail?.conflicts
      const notReturned = job.detail?.not_returned
      if (job.job_type === 'user' && Array.isArray(conflicts) && conflicts.length) {
        Toast.warning(`成员同步完成，${conflicts.length} 人的手机号或账号绑定有冲突，请人工核对`)
      } else if (job.job_type === 'user' && Array.isArray(notReturned) && notReturned.length) {
        Toast.warning(`成员同步完成，${notReturned.length} 人本次未返回，原绑定已保留`)
      } else if (job.status === 'partial' || job.fail_count > 0) {
        Toast.warning(`同步未全部完成：成功 ${job.success_count} 项，失败 ${job.fail_count} 项，请查看同步记录`)
      } else Toast.success(`同步完成，成功 ${job.success_count} 项`)
      refresh()
    },
    onError: (error: Error) => {
      Toast.error(isWeComIpDenied(error.message) ? WECOM_IP_DENIED_NOTICE : error.message)
      // 整批外部失败也会保存失败任务；刷新列表，避免错误提示后仍显示旧记录。
      void queryClient.invalidateQueries({ queryKey: ['wecom-jobs'] })
    },
  })

  const importPreviewMutation = useMutation({
    mutationFn: previewWeComCustomerImport,
    onSuccess: (preview) => {
      setImportPreview(preview)
      importCustomersMutation.reset()
    },
    onError: (error: Error) => { if (!importPreview) Toast.error(error.message) },
  })
  const importCustomersMutation = useMutation({
    mutationFn: importWeComCustomers,
    onSuccess: (result) => {
      Toast.success(`已将 ${result.import_contacts} 条联系人导入客户中心，${result.held_contacts} 条保留待确认`)
      setImportPreview(null)
      refresh()
    },
  })
  const staleImportPreview = importCustomersMutation.error instanceof ApiError && importCustomersMutation.error.status === 409
  const importBlocked = staleImportPreview ? '这份预览已过期，请重新预览。'
    : importPreviewMutation.isPending ? '正在更新导入预览。'
      : !importPreview?.import_contacts ? '当前没有可导入的联系人。'
        : ''

  const bindMutation = useMutation({
    mutationFn: ({ contactId, customerId }: { contactId: number; customerId: number }) =>
      bindUnboundContact(contactId, { customer_id: customerId }),
    onSuccess: (result) => {
      setManualBindContact(null)
      // 改挂（原来挂在别的客户上）时把**历史去向**一起说清楚（主人 2026-10-12 定：
      // 跨客户改挂合法、不拦，但跟进记录留在原客户那儿，必须讲明白）。
      if (result.rebound_from_customer_id) {
        Toast.info(
          `已把该联系人改挂到「${result.customer_name}」；他在原来那家的 `
          + `${result.stayed_followup_count ?? 0} 条跟进记录仍留在原客户那里（不会跟着走）`,
        )
      } else {
        Toast.success(`已关联到客户「${result.customer_name}」`)
      }
      refresh()
    },
    onError: (error: Error) => { if (!manualBindContact) Toast.error(error.message) },
  })

  const createMutation = useMutation({
    mutationFn: (contactId: number) =>
      createCustomerFromContact(contactId, {
        name: createForm.name.trim(),
        region: createForm.region || null,
        level: createForm.level || null,
      }),
    onSuccess: (result) => {
      Toast.success(`已创建客户「${result.customer_name}」`)
      setCreateContact(null)
      setCreatedCustomer(result)
      setCreateForm({ name: '', region: '', level: '' })
      refresh()
    },
  })

  const ignoreMutation = useMutation({
    mutationFn: (contact: WeComUnboundContact) => ignoreUnboundContact(contact.id, '界面标记暂不处理'),
    onSuccess: (_, contact) => {
      Toast.success(`「${contact.name || '未命名联系人'}」已移出待确认`)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const resetTransfer = () => {
    setTransferVisible(false)
    setTransferStage('form')
    setTransferPreview(null)
    setTransferJob(null)
    setTransferResultPage(1)
    setTransferResultFilter('all')
    setTransferAssignees({})
    setTransferForm({})
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
    onSuccess: (job) => {
      const detail = (job.detail ?? {}) as Record<string, unknown>
      const failed = Number(detail.failures_count ?? 0)
      const frozen = Number(detail.frozen ?? 0)
      // 并发下被同事先接走的客户（第九批复审 P1）：后端按"跳过"处理、保留同事的归属，
      // 这里必须如实报出来 —— 否则操作者以为客户都交接完了，实际有几个留在了别人名下。
      const skipped = Number(detail.customers_skipped ?? 0)
      // **按实际结果说人话**：不再笼统报"继承完成"。
      // 部分成功时要说清成功了多少、还剩多少、下一步去哪处理。
      const parts: string[] = []
      if (frozen) parts.push(`${frozen} 个客户因撞单争议被冻结`)
      if (skipped) parts.push(`${skipped} 个客户已由其他同事接手（保留其归属）`)
      if (failed) parts.push(`${failed} 项没成`)
      if (failed || frozen) {
        Toast.warning(`交接结果：${parts.join('、')}。请查看逐项结果`)
      } else if (skipped) {
        // 没有"待处理"的项（跳过的项是终态，不会出现在下面的未完成清单里），
        // 所以别再说"可在下方逐项处理"，免得操作者去下面找却找不到
        Toast.warning(`交接完成：${parts.join('、')}`)
      } else {
        Toast.success('交接完成')
      }
      setTransferJob(job)
      setTransferResultPage(1)
      setTransferStage('result')
      refresh()
      void queryClient.invalidateQueries({ queryKey: ['customers'] })
      void queryClient.invalidateQueries({ queryKey: ['opportunities'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const retryMutation = useMutation({
    mutationFn: () => retryWeComTransfer(transferJob!.id),
    onSuccess: async (result) => {
      // **任务汇总跟着重试结果一起刷新**（返修单第六批第 11 条）：
      // 此前只重拉了"未完成项"列表，任务上的成功数/失败数还是重试前的值 ——
      // 于是页面会一边显示"还剩 0 项"、一边挂着旧的"失败若干项"，两边对不上。
      // 后端返回的就是按逐项最终状态重算过的数，直接用，不在这里另算一套。
      setTransferJob((prev) =>
        prev
          ? {
              ...prev,
              status: result.failed ? 'partial' : 'success',
              status_label: result.failed ? '部分失败' : '成功',
              success_count: result.succeeded,
              fail_count: result.failed,
              detail: { ...(prev.detail ?? {}), remaining: result.remaining },
            }
          : prev,
      )
      Toast.success(
        `已重试 ${result.retried} 项，还剩 ${result.remaining} 项未完成`
          + (result.failed ? `（累计失败 ${result.failed} 项）` : ''),
      )
      setTransferResultPage(1)
      await queryClient.invalidateQueries({ queryKey: transferResultQueryKey })
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const reviewDisputeLink = (customerId: number, handoverId: unknown, takeoverId: unknown) => {
    if (!canViewDisputes) return null
    const selection = typeof handoverId === 'number' && typeof takeoverId === 'number'
      ? { handover_user_id: handoverId, takeover_user_id: takeoverId }
      : undefined
    return <Link to={`/duplicate-cases?customer_id=${customerId}`} style={{ color: 'var(--crm-primary)' }}
      state={{ fromWeComTransfer: true, selection }}>
      查看待裁定记录
    </Link>
  }

  const transferBusy = previewMutation.isPending || transferMutation.isPending || retryMutation.isPending
  const eligibleTransferCount = transferPreview?.sections.reduce((count, section) =>
    count + section.items.filter((item) =>
      !item.blocked_reason && (section.kind !== 'wecom_relation' || readinessQuery.data?.can_sync_external),
    ).length, 0,
  ) ?? 0
  const transferBlockedReason = !canExecuteTransfer
    ? '你可以核对清单。请联系有交接权限的同事执行。'
    : readinessQuery.isError
      ? '暂时无法确认交接设置，请重新读取。'
      : !readinessQuery.data
        ? '正在读取交接设置。'
        : !readinessQuery.data.transfer_enabled
          ? '系统尚未开放执行交接。可先核对清单，需要执行时请联系管理员。'
          : !eligibleTransferCount
            ? '本次没有可交接的事项。可返回更换交接人，或先处理清单里的限制。'
            : undefined

  const readiness = readinessQuery.data
  const counts = readiness?.counts
  const lastFollowSync = followJobQuery.data?.items[0]
  const followRunning = lastFollowSync?.status === 'running'
  const busy = syncMutation.isPending || importPreviewMutation.isPending
    || importCustomersMutation.isPending || followRunning
  // 后台同步从「进行中」变为完成/失败时，刷新页面上的统计与清单
  const followStatusRef = useRef<string | undefined>(undefined)
  useEffect(() => {
    const status = lastFollowSync?.status
    if (followStatusRef.current === 'running' && status && status !== 'running') refresh()
    followStatusRef.current = status
    // refresh 每次渲染都是新函数，放进依赖会重复触发
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastFollowSync?.status])
  const nextSync = counts && readiness && (
    !counts.wecom_users && canSyncDirectory && readiness.can_sync_contact ? SYNC_ACTIONS.users
      : !counts.external_contacts && canSyncContacts && readiness.can_sync_external ? SYNC_ACTIONS.external
        : counts.unbound_contacts > 0 && !counts.follow_relations && canSyncContacts && readiness.can_sync_external ? SYNC_ACTIONS.follow
          : undefined
  )
  const previewButton = (primary: boolean) => <Button type="primary" theme={primary ? 'solid' : 'light'}
    disabled={busy} loading={importPreviewMutation.isPending}
    onClick={() => importPreviewMutation.mutate()}>预览导入</Button>
  let guideTitle = '整理企微客户'
  let guideText = '选择左侧联系人，核对后关联已有客户或创建客户档案。'
  if (canSync || canPreviewImport) {
    if (readinessQuery.isError) {
      guideText = '暂时无法读取同步状态，可以重新检查后再继续。'
    } else if (!counts) {
      guideText = '正在检查同步状态…'
    } else if (nextSync === SYNC_ACTIONS.users) {
      guideTitle = '先同步企微成员'
      guideText = '同步后可把企微跟进员工与 CRM 账号对应起来，导入时才能按原跟进员工分配。'
    } else if (nextSync === SYNC_ACTIONS.external) {
      guideTitle = '同步外部联系人'
      guideText = '拉取企微联系人资料后，再同步跟进关系。'
    } else if (nextSync === SYNC_ACTIONS.follow) {
      guideTitle = '补齐跟进关系'
      guideText = `还有 ${counts.unbound_contacts.toLocaleString()} 位联系人待确认。同步跟进关系，查清他们由谁负责。`
    } else if (counts.unbound_contacts > 0) {
      guideTitle = '核对客户导入预览'
      guideText = canImport ? '预览会列出新建、关联和待确认的记录。只有确认后才会写入客户中心。'
        : canPreviewImport ? '你可以核对导入预览，确认导入由有权限的同事操作。'
          : '可逐条核对联系人。批量导入请联系有权限的同事。'
    } else {
      guideText = '当前没有待确认联系人。有新联系人时，可从右上角更新企微数据。'
    }
  }

  return (
    <div className="page-container">
      <PageHeader
        className="wecom-header"
        title="企业微信"
        subtitle="将企微联系人整理到客户中心"
        extra={(canSync || canPreviewTransfer) && <>
          {canSync && <Dropdown trigger="click" position="bottomRight" clickToHide render={
            <Dropdown.Menu>
              {Object.values(SYNC_ACTIONS).filter(action => action.scope === 'contact' ? canSyncDirectory : canSyncContacts).map(action => {
                const available = action.scope === 'contact' ? readiness?.can_sync_contact : readiness?.can_sync_external
                return <Dropdown.Item key={action.label} disabled={busy || available === false}
                  onClick={() => syncMutation.mutate(action.run)}>
                  <div style={{ padding: '4px 0' }}>
                    <div>{action.label}</div>
                    <small style={{ color: 'var(--crm-text-3)' }}>
                      {available === false ? `需要配置企微${action.scope === 'contact' ? '通讯录' : '客户联系'}权限` : action.description}
                    </small>
                  </div>
                </Dropdown.Item>
              })}
            </Dropdown.Menu>
          }>
            <Button theme="borderless" loading={syncMutation.isPending || followRunning}>更新企微数据 ▾</Button>
          </Dropdown>}
          {canPreviewTransfer && <Button type="tertiary" theme="borderless" onClick={() => setTransferVisible(true)}>离职交接</Button>}
        </>}
      />

      <SectionCard style={{ marginBottom: 12 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
          <div role="status" style={{ flex: '1 1 270px' }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>{guideTitle}</div>
            <div style={{ color: 'var(--crm-text-2)', lineHeight: 1.6 }}>{guideText}</div>
            {canSyncContacts && readiness && !readiness.can_sync_external && <div style={{ color: 'var(--crm-text-3)', marginTop: 4 }}>
              当前无法更新外部联系人和跟进关系，请联系管理员检查企微客户联系配置。
            </div>}
            {canSyncContacts && counts?.follow_relations === 0 && isWeComIpDenied(lastFollowSync?.error_message) &&
              <div style={{ color: 'var(--crm-text-3)', marginTop: 4 }}>上次同步跟进关系失败。{WECOM_IP_DENIED_NOTICE}</div>}
          </div>
          {(canSync || canPreviewImport) && <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
            {readinessQuery.isError && <Button onClick={() => void readinessQuery.refetch()}>重新检查</Button>}
            {counts && counts.unbound_contacts > 0 && canPreviewImport && previewButton(!nextSync)}
            {nextSync && <Button type="primary" theme="solid" disabled={busy}
              loading={syncMutation.isPending && syncMutation.variables === nextSync.run}
              onClick={() => syncMutation.mutate(nextSync.run)}>{nextSync.label}</Button>}
          </div>}
        </div>
      </SectionCard>

      {/* 五项指标按全局 4 列网格排，第五张「未建账号」被挤到第二行左边孤零零一张，
          下面又接着三栏卡片 —— 中间多出一条断掉的半行。
          这页正好五项，直接给五列，一行收完。 */}
      <KpiStrip
        className="kpi-grid kpi-cols-5 wecom-kpi"
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
          { label: '跟进关系', value: readiness?.counts.follow_relations ?? '-', hint: '企微员工与联系人的关系' },
          { label: '待确认联系人', value: readiness?.counts.unbound_contacts ?? '-', hint: '建档或关联前待核对' },
          {
            label: '未匹配账号',
            value: readiness?.counts.unmatched_users ?? '-',
            hint: '尚未对应 CRM 账号的企微成员',
          },
        ]}
      />

      {/* 三栏原来写死 300px + 320px，窗口一窄就先把自己撑破（整页横向滚动） */}
      {createdCustomer && (
        <Banner type="success" fullMode={false} onClose={() => setCreatedCustomer(null)}
          description={`已创建「${createdCustomer.customer_name}」并关联企微联系人。`}>
          {can('customer:view') && <Link to={`/customers/${createdCustomer.customer_id}`}>查看新客户</Link>}
        </Banner>
      )}
      <div className="split-multi wecom-split" style={{ gap: 16 }}>
        {/* 左：待处理外部联系人 */}
        <SectionCard id="wecom-contact-review">
          <div className="crm-help-label" style={{ fontWeight: 600, marginBottom: 10 }}>
            待确认联系人
            <HelpTip forLabel="待确认联系人" text="这些是已同步的企微联系人。确认后可以关联到已有客户，也可以新建客户档案。" />
          </div>
          {rows.some((row) => row.import_hold_reason) && (
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 10 }}>
              标出原因的不会随「预览导入」批量进入客户中心，点开可看原因和处理办法。
            </div>
          )}
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
            <Empty description={listQuery.isLoading ? '加载中…' : '没有待确认客户'} />
          ) : (
            // 列表自己滚，不把整页往下拉：这一栏原来有多少人就有多长，
            // 一页 20 条会把三栏卡片撑到视口外面，中右两栏跟着变成一整块空白。
            <div style={{ display: 'grid', gap: 6, maxHeight: 'min(40vh, 392px)', overflowY: 'auto', paddingRight: 2 }}>
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
                  {row.import_hold_reason && (
                    <div
                      title={row.import_hold_reason}
                      style={{
                        fontSize: 11.5,
                        color: 'var(--crm-warning)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {row.import_hold_reason}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
          <div style={{ marginTop: 10, textAlign: 'right' }}>
            {/* 列表底下翻页的两个小按钮也带描边，像两个胶囊并排（2026-11-20）→ 文字按钮 */}
            <Button
              size="small"
              type="tertiary"
              theme="borderless"
              disabled={page <= 1}
              onClick={() => setPage((current) => Math.max(1, current - 1))}
            >
              上一页
            </Button>
            <Button
              size="small"
              type="tertiary"
              theme="borderless"
              style={{ marginLeft: 6 }}
              disabled={rows.length < 20}
              onClick={() => setPage((current) => current + 1)}
            >
              下一页
            </Button>
          </div>
        </SectionCard>

        {/* 中栏 + 右栏收进同一个右半区，上下叠：详情在上、疑似客户在下。
            原来三栏并排，中栏 220px、右栏 190px，左栏名单却有 570px ——
            右边两栏底下拖出一大块灰底空白，三张卡底边也互相对不齐
            （2026-11-18 主人："空的太多了，也没有对齐"）。叠起来以后右半区
            自己成一块，左边名单多长都不会把右边撕成两截。 */}
        <div className="wecom-side">
        {/* 中：企业微信详情。纵向弹性 + 底下那块 margin-top auto：
            卡片被拉到与左栏等高后，内容不会全挤在上半截、下半截空一大片。 */}
        <SectionCard style={{ display: 'flex', flexDirection: 'column' }}>
          {!active ? (
            <Empty description="请先在待确认列表选择联系人" />
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
                  <span style={{ color: 'var(--crm-text-3)', minWidth: 84 }}>同步时间</span>
                  <span>
                    {active.last_sync_at
                      ? formatBeijingTime(active.last_sync_at)
                      : '-'}
                  </span>
                </div>
              </div>

              <div style={{ marginTop: 'auto', paddingTop: 14 }}>
                {active.import_hold_reason && (
                  <div style={{
                    marginBottom: 12,
                    padding: '8px 10px',
                    borderRadius: 'var(--crm-radius-sm)',
                    background: 'var(--crm-warning-bg)',
                    border: '1px solid var(--crm-warning-border)',
                    color: 'var(--crm-warning)',
                    fontSize: 12.5,
                  }}>
                    <div style={{ fontWeight: 600 }}>这条不会随「批量导入」进入客户中心</div>
                    <div style={{ marginTop: 2 }}>
                      {active.import_hold_reason}。{IMPORT_REVIEW_GUIDANCE[active.import_hold_reason] ?? '请核对后在右侧逐条确认。'}
                    </div>
                  </div>
                )}
                <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>跟进员工</div>
                {active.followers.length === 0 ? (
                  <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>
                    这位联系人暂无企微跟进员工记录。{canSyncContacts && '可在「更新企微数据」中同步跟进关系。'}
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
                        <span>{follower.name || '姓名待同步'}</span>
                        <span style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
                          {follower.crm_user_id ? `CRM：${follower.crm_user_name ?? '已绑定'}` : '未开通 CRM'}
                        </span>
                        <span style={{ color: 'var(--crm-text-3)', marginLeft: 'auto' }}>
                          {follower.add_time
                            ? formatBeijingDate(follower.add_time)
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

        {/* 右：系统候选客户。候选列表在上、两个操作按钮贴底 ——
            否则卡片拉高以后，按钮会悬在半空、下面空出一截。 */}
        <SectionCard style={{ display: 'flex', flexDirection: 'column' }}>
          <div style={{ fontWeight: 600, marginBottom: 10 }}>可能匹配的 CRM 客户</div>
          {!active ? (
            <Empty description="选中联系人后显示候选" />
          ) : (
            <>
              {!canViewCustomers && <Empty description="需要客户查看权限才能查看匹配建议" />}
              {canViewCustomers && candidatesQuery.isLoading && (
                <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>匹配中…</div>
              )}
              {canViewCustomers && candidatesQuery.isError && <div style={{ color: 'var(--crm-error)', fontSize: 13, marginBottom: 10 }}>
                暂时无法查询已有客户。<Button size="small" theme="borderless" onClick={() => void candidatesQuery.refetch()}>重试</Button>
              </div>}
              {canViewCustomers && candidatesQuery.isSuccess && candidatesQuery.data.candidates.length === 0 && (
                // 原来是一句灰字缩在左上角，这张卡又被右半区拉到半屏高，
                // 中间空出近 200px、按钮孤零零贴在底下（2026-11-21 巡检）。
                // 改成居中的两行字：用 margin auto 在剩余空间里站中间，不写 flex:1
                // （写了会把 1fr 的行高顶破，右半区变长、底部按钮掉出屏幕）。
                <div style={{ margin: 'auto 0', padding: '8px 0', textAlign: 'center' }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--crm-text-2)' }}>
                    当前查看范围内没有匹配建议
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginTop: 4 }}>
                    {canSearchCustomers ? '可按客户名称搜索现有档案，核对后再关联或新建。' : '可先核对客户中心的已有档案。'}
                  </div>
                </div>
              )}
              <div style={{ display: 'grid', gap: 8 }}>
                {(canViewCustomers ? candidatesQuery.data?.candidates ?? [] : []).map((candidate, index) => (
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
                    {canBindCustomers && (
                      <Popconfirm title={`确认关联客户「${candidate.name}」？`} content="请核对匹配信息，确认是同一位客户。" disabled={bindMutation.isPending} onConfirm={() => bindMutation.mutate({ contactId: active.id, customerId: candidate.id })}>
                        <Button size="small" theme="outline" type="primary" style={{ marginTop: 8 }} loading={bindMutation.isPending}>关联已有客户</Button>
                      </Popconfirm>
                    )}
                  </div>
                ))}
              </div>

              {canManage && active && (
                // 两个出口原来各占一整行、通栏灰块，像张表单；并排放在一行就够了。
                // marginTop:auto：卡片被拉到半栏高时，按钮贴底，不悬在中间。
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, paddingTop: 12, marginTop: 'auto' }}>
                  {canSearchCustomers && (
                    <Button theme="outline" onClick={() => {
                      bindMutation.reset()
                      setCustomerSearch('')
                      setCustomerKeyword('')
                      setBindTarget(null)
                      setManualBindContact(active)
                    }}>搜索已有客户</Button>
                  )}
                  {canCreateCustomers && <Button
                    onClick={() => {
                      createMutation.reset()
                      setCreateForm({
                        name: active.corp_name || active.name || '',
                        region: '',
                        level: '',
                      })
                      setCreateContact(active)
                    }}
                  >
                    创建新客户
                  </Button>}
                  <Popconfirm title={`将「${active.name || '未命名联系人'}」移出待确认？`}
                    content="企微联系人资料会保留，这条记录将从待确认列表移出。"
                    disabled={ignoreMutation.isPending} onConfirm={() => ignoreMutation.mutate(active)}>
                    <Button theme="borderless" loading={ignoreMutation.isPending}>移出待确认</Button>
                  </Popconfirm>
                </div>
              )}
            </>
          )}
        </SectionCard>
        </div>
      </div>

      <SectionCard style={{ marginTop: 16 }}>
        <div style={{ fontWeight: 600, marginBottom: 12 }}>同步任务</div>
        <Table<WeComSyncJob>
          columns={[
            {
              title: '类型', dataIndex: 'job_type_label', width: 145,
              render: (label: string, job: WeComSyncJob) => (
                <div>
                  <div>{label}</div>
                  {canExecuteTransfer && job.job_type === 'transfer' && (
                    <Button size="small" theme="borderless" type="primary" onClick={() => {
                      setTransferJob(job)
                      setTransferResultPage(1)
                      setTransferStage('result')
                      setTransferVisible(true)
                    }}>查看交接结果</Button>
                  )}
                </div>
              ),
            },
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
              render: (value: string) => formatBeijingTime(value),
            },
            {
              title: '说明',
              dataIndex: 'error_message',
              render: (value: string | null, record: WeComSyncJob) => {
                const conflicts = record.detail?.conflicts
                const notReturned = record.detail?.not_returned
                if (record.job_type === 'user') {
                  const notes = []
                  if (Array.isArray(conflicts) && conflicts.length) notes.push(`${conflicts.length} 人的账号绑定有冲突，待人工核对`)
                  if (Array.isArray(notReturned) && notReturned.length) {
                    const names = notReturned.slice(0, 5).join('、')
                    notes.push(`本次未返回 ${notReturned.length} 人，原绑定保留（${names}${notReturned.length > 5 ? '等' : ''}）`)
                  }
                  if (notes.length) return notes.join('。')
                }
                if (isWeComIpDenied(value)) return WECOM_IP_DENIED_NOTICE
                return value ?? '-'
              },
            },
          ]}
          dataSource={jobsQuery.data?.items ?? []}
          rowKey="id"
          size="small"
          pagination={false}
          loading={jobsQuery.isLoading}
          empty={jobsQuery.isError ? '暂时无法读取同步任务' : '还没有同步记录'}
        />
        {jobsQuery.isError && <Button size="small" onClick={() => void jobsQuery.refetch()}>重新读取同步任务</Button>}
        {(jobsQuery.data?.total ?? 0) > 10 && (
          <div className="wecom-transfer-pagination">
            <Button size="small" disabled={jobsPage === 1} onClick={() => setJobsPage(jobsPage - 1)}>上一页同步任务</Button>
            <span>第 {jobsPage} 页</span>
            <Button size="small" disabled={jobsPage * 10 >= jobsQuery.data!.total} onClick={() => setJobsPage(jobsPage + 1)}>下一页同步任务</Button>
          </div>
        )}
      </SectionCard>

      <Modal
        width={520}
        title="创建新客户"
        className="wecom-import-modal"
        visible={Boolean(createContact)}
        onCancel={() => { if (!createMutation.isPending) setCreateContact(null) }}
        footer={
          <div className="workflow-modal-actions">
            <Button disabled={createMutation.isPending} onClick={() => setCreateContact(null)}>取消</Button>
            <GuardedButton theme="outline" action="创建客户" disabled={!createForm.name.trim()}
              blockedReasons={['请填写客户名称。']} hintBelow loading={createMutation.isPending}
              onClick={() => { if (createContact && createForm.name.trim()) createMutation.mutate(createContact.id) }}>
              创建并关联
            </GuardedButton>
          </div>
        }
      >
        <div className="receivable-picker">
          <p className="receivable-picker-guide">将建档并关联企微联系人 <strong>{createContact?.name || '未命名联系人'}</strong>。</p>
          <div>
            <FormLabel required>客户名称</FormLabel>
            <Input
              aria-label="客户名称"
              value={createForm.name}
              disabled={createMutation.isPending}
              onChange={(value) => setCreateForm({ ...createForm, name: value })}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>地区</div>
            <Input
              value={createForm.region}
              disabled={createMutation.isPending}
              onChange={(value) => setCreateForm({ ...createForm, region: value })}
              placeholder="例如：华东"
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>客户等级</div>
            <Select
              value={createForm.level || undefined}
              disabled={createMutation.isPending}
              onChange={(value) => setCreateForm({ ...createForm, level: value as string })}
              optionList={(customerLevelsQuery.data ?? []).map((item) => ({ value: item.code, label: item.label }))}
              showClear
              style={{ width: '100%' }}
            />
          </div>
          <p className="receivable-picker-guide">新档案的来源记为「企业微信」。</p>
          {createMutation.error && (
            <Banner type="danger" fullMode={false} closeIcon={null} description={createMutation.error.message} />
          )}
        </div>
      </Modal>

      <Modal
        title="搜索并关联已有客户"
        width={620}
        className="wecom-import-modal"
        visible={Boolean(manualBindContact)}
        onCancel={() => { if (!bindMutation.isPending) setManualBindContact(null) }}
        footer={
          <div className="workflow-modal-actions">
            <Button disabled={bindMutation.isPending} onClick={() => setManualBindContact(null)}>取消</Button>
            <GuardedButton
              theme="outline"
              action="确认关联"
              disabled={!bindTarget}
              blockedReasons={['请选择要关联的客户。']}
              hintBelow
              loading={bindMutation.isPending}
              onClick={() => {
                if (manualBindContact && bindTarget) bindMutation.mutate({ contactId: manualBindContact.id, customerId: bindTarget.id })
              }}
            >
              确认关联
            </GuardedButton>
          </div>
        }
      >
        {manualBindContact && (
          <div className="receivable-picker">
            <p className="receivable-picker-guide">正在核对企微联系人 <strong>{manualBindContact.name || '未命名联系人'}</strong></p>
            <div>
              <FormLabel>客户名称</FormLabel>
              <div className="wecom-customer-search">
                <Input
                  aria-label="客户名称"
                  placeholder="输入客户全称或简称"
                  value={customerSearch}
                  disabled={bindMutation.isPending}
                  onChange={value => { setCustomerSearch(value); setBindTarget(null); bindMutation.reset() }}
                  onEnterPress={searchForCustomer}
                />
                <Button loading={matchingCustomersQuery.isFetching} disabled={!customerSearch.trim() || bindMutation.isPending}
                  onClick={searchForCustomer}>查询客户</Button>
              </div>
            </div>
            {matchingCustomersQuery.isError && (
              <Banner type="danger" fullMode={false} closeIcon={null} description={matchingCustomersQuery.error.message}>
                <Button size="small" onClick={() => void matchingCustomersQuery.refetch()}>重新查询</Button>
              </Banner>
            )}
            {customerKeyword === customerSearch.trim() && matchingCustomersQuery.isSuccess && (
              <>
                {matchingCustomersQuery.data.items.length === 0 && (
                  <p className="receivable-picker-guide">没有找到可关联的客户。请核对名称或换一个关键词。</p>
                )}
                {matchingCustomersQuery.data.items.map(customer => (
                  <div className="wecom-customer-result" key={customer.id}>
                    <div>
                      <strong>{customer.name}</strong>
                      <p className="receivable-picker-guide">
                        {[customer.region, customer.owner_name ? `负责人 ${customer.owner_name}` : null].filter(Boolean).join(' · ')}
                      </p>
                    </div>
                    <Button size="small" theme="outline" aria-pressed={bindTarget?.id === customer.id} disabled={bindMutation.isPending}
                      onClick={() => { setBindTarget(customer); bindMutation.reset() }}>
                      {bindTarget?.id === customer.id ? '已选择' : '选择此客户'}
                    </Button>
                  </div>
                ))}
                {matchingCustomersQuery.data.total > matchingCustomersQuery.data.items.length &&
                  <p className="receivable-picker-guide">当前显示前 20 条，可输入更完整的名称缩小范围。</p>}
              </>
            )}
            {bindTarget && (
              <Banner type="info" fullMode={false} closeIcon={null}
                description={`${manualBindContact.name || '未命名联系人'} 将关联到 ${bindTarget.name}`} />
            )}
            {bindMutation.error && (
              <Banner type="danger" fullMode={false} closeIcon={null} description={bindMutation.error.message} />
            )}
          </div>
        )}
      </Modal>

      <Modal
        title="预览导入客户中心"
        width={620}
        className="wecom-import-modal"
        visible={Boolean(importPreview)}
        onCancel={() => { if (!importCustomersMutation.isPending) setImportPreview(null) }}
        footer={
          <div className="workflow-modal-actions">
            <Button disabled={importCustomersMutation.isPending} onClick={() => setImportPreview(null)}>关闭预览</Button>
            {canImport && (
              <GuardedButton
                theme="outline"
                action="确认导入"
                disabled={Boolean(importBlocked)}
                blockedReasons={importBlocked ? [importBlocked] : []}
                loading={importCustomersMutation.isPending}
                hintBelow
                onClick={() => { if (importPreview && !importBlocked) importCustomersMutation.mutate(importPreview.digest) }}
              >
                确认导入
              </GuardedButton>
            )}
          </div>
        }
      >
        {importPreview && (
          <div className="receivable-picker">
            {staleImportPreview ? (
              <Banner type="warning" fullMode={false} closeIcon={null} title="导入预览已过期"
                description="客户资料或负责人已变化。请刷新预览，重新核对后再确认导入。">
                <Button size="small" theme="outline" loading={importPreviewMutation.isPending}
                  onClick={() => importPreviewMutation.mutate()}>重新预览</Button>
              </Banner>
            ) : importCustomersMutation.error && (
              <Banner type="danger" fullMode={false} closeIcon={null} title="导入未完成" description={importCustomersMutation.error.message} />
            )}
            {importPreviewMutation.error && (
              <Banner type="danger" fullMode={false} closeIcon={null} description={importPreviewMutation.error.message} />
            )}
            <div className="wecom-import-summary">
              <div><span>可导入联系人</span><strong>{importPreview.import_contacts.toLocaleString()} 位</strong></div>
              <div><span>待人工确认</span><strong>{importPreview.held_contacts.toLocaleString()} 位</strong></div>
            </div>
            {importPreview.import_contacts > 0 && (
              <p className="receivable-picker-guide">
                新建公司客户 {importPreview.new_company_customers} 个、个人客户 {importPreview.new_personal_customers} 个，
                关联已有客户的联系人 {importPreview.existing_customer_contacts} 位。
                新建客户由匹配到的原跟进员工负责，关联时沿用已有客户的负责人。
              </p>
            )}
            {!canImport && <p className="receivable-picker-guide">需要有客户导入、创建和分配权限的同事确认导入。</p>}
            {importPreview.held_contacts > 0 && (
              <>
                <strong>待确认原因与处理方式</strong>
                <dl className="wecom-import-reasons">
                  {Object.entries(importPreview.held_reasons).sort(([, a], [, b]) => b - a).map(([reason, count]) => (
                    <div key={reason}>
                      <dt>{reason}<strong>{count.toLocaleString()} 位</strong></dt>
                      <dd>{IMPORT_REVIEW_GUIDANCE[reason] ?? '请逐条核对联系人资料，再重新预览。'}</dd>
                    </div>
                  ))}
                </dl>
                <Button size="small" theme="borderless" style={{ justifySelf: 'start' }} disabled={importCustomersMutation.isPending} onClick={() => {
                  setImportPreview(null)
                  document.getElementById('wecom-contact-review')?.scrollIntoView({ block: 'start' })
                }}>打开待确认联系人</Button>
              </>
            )}
          </div>
        )}
      </Modal>

      <Modal
        title={transferStage === 'result' ? '离职交接结果' : '离职交接'}
        className="workflow-scroll-modal"
        visible={transferVisible && canPreviewTransfer}
        onCancel={() => { if (!transferBusy) resetTransfer() }}
        footer={
          transferStage === 'form' ? (
            <>
              <Button disabled={transferBusy} onClick={resetTransfer}>取消</Button>
              <Button type="primary"
                theme="outline"
                loading={previewMutation.isPending}
                disabled={!selectedHandover || !takeoverPeople.some(
                  (person) => person.id === transferForm.takeover_user_id,
                )}
                onClick={() => previewMutation.mutate()}
              >
                查看交接清单
              </Button>
            </>
          ) : transferStage === 'preview' ? (
            <>
              <Button disabled={transferBusy} onClick={() => setTransferStage('form')}>返回修改</Button>
              {canExecuteTransfer && <Button type="primary"
                theme="outline"
                loading={transferMutation.isPending}
                disabled={Boolean(transferBlockedReason)}
                onClick={() => transferMutation.mutate()}
              >
                执行交接
              </Button>}
            </>
          ) : (
            <>
              <Button disabled={transferBusy} onClick={resetTransfer}>关闭</Button>
              {canExecuteTransfer && readiness?.transfer_enabled && transferPendingQuery.isSuccess && transferOpenTotal > 0 && (
                <Button type="primary"
                  theme="outline"
                  loading={retryMutation.isPending}
                  onClick={() => retryMutation.mutate()}
                >
                  重试未完成的 {transferOpenTotal} 项
                </Button>
              )}
            </>
          )
        }
        width="min(680px, calc(100vw - 24px))"
      >
        {transferStage === 'form' && (
          <div style={{ display: 'grid', gap: 12 }}>
            {usersQuery.isSuccess && transferForm.handover_user_id && !selectedHandover && (
              <div>原交接人已不在可选人员范围内，请重新选择。</div>
            )}
            {usersQuery.isError && <div role="alert">无法读取交接人员。<Button size="small" theme="borderless" onClick={() => void usersQuery.refetch()}>重试</Button></div>}
            {usersQuery.isSuccess && !usersQuery.data.length && <div>当前没有可选人员。跨部门交接请联系管理员。</div>}
            <div>
              <FormLabel required>交接人（离职）</FormLabel>
              <Select
                value={transferForm.handover_user_id}
                placeholder="选择要交出工作的同事"
                disabled={transferBusy}
                onChange={(value) => {
                  setTransferAssignees({})
                  setTransferForm({
                    handover_user_id: value as number,
                    takeover_user_id: transferForm.takeover_user_id === value
                      ? undefined : transferForm.takeover_user_id,
                  })
                }}
                optionList={transferPeople.map((user) => ({
                  value: user.id,
                  label: `${user.name}（${user.username}）${user.status === 'active' ? '' : ' · 已停用'}`,
                }))}
                filter={optionMatcher}
                style={{ width: '100%' }}
              />
            </div>
            <div>
              <FormLabel required>接管人</FormLabel>
              <Select
                value={transferForm.takeover_user_id}
                placeholder="选择接手工作的在职同事"
                disabled={!transferForm.handover_user_id || transferBusy}
                onChange={(value) =>
                  setTransferForm({ ...transferForm, takeover_user_id: value as number })
                }
                optionList={takeoverPeople.map((user) => ({
                  value: user.id,
                  label: `${user.name}（${user.username}）`,
                }))}
                filter={optionMatcher}
                style={{ width: '100%' }}
              />
            </div>
            {transferForm.handover_user_id && !takeoverPeople.length && (
              <div>当前没有可接手的在职同事。请联系管理员核对人员。</div>
            )}
            <div className="wecom-transfer-note">
              先选两位同事，再核对客户、商机、待办和单据的交接清单。
              <br />
              会保留：创建人、历史跟进、历史报价、审批与审计日志、签单业绩归属；
              客户名下其他在职同事的待办也不动。

            </div>
          </div>
        )}

        {transferStage === 'preview' && transferPreview && (
          <div style={{ display: 'grid', gap: 10 }}>
            <div className="wecom-transfer-note">
              {readinessQuery.data && <div><b>交接范围</b> · {transferMode}</div>}
              {transferBlockedReason && <div>{transferBlockedReason}</div>}
              {readinessQuery.isError && <Button size="small" onClick={() => void readinessQuery.refetch()}>重新读取设置</Button>}
            </div>
            <div style={{ fontSize: 13.5 }}>
              「{transferPreview.handover.name}」名下共 <b>{transferPreview.total}</b> 项，
              本次可交接 <b>{eligibleTransferCount}</b> 项。接管人为「{transferPreview.takeover.name}」。
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
              {/* 清单可能不完整时的提示（例如离职人没绑企微 → 企微客户关系列不出来）。
                  与「本次冻结」（blocked_reason）语义不同：那些是**确定不交接**，
                  这些是"你得先处理某事，否则这次会漏"（2026-10-14 审查新增）。 */}
              {readinessQuery.data?.can_sync_external && (transferPreview.warnings || []).map((text) => (
                <Banner key={text} type="warning" closeIcon={null} description={text} />
              ))}
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
                          className="wecom-transfer-row"
                        >
                          <span style={{ flex: 1 }}>
                            · {item.label}
                            {item.blocked_reason ? (
                              <Tag color="orange" size="small" style={{ marginLeft: 6 }}>
                                本次冻结
                              </Tag>
                            ) : item.kind === 'wecom_relation' && !readinessQuery.data?.can_sync_external ? (
                              <Tag color="grey" size="small" style={{ marginLeft: 6 }}>本次不转企微</Tag>
                            ) : null}
                          </span>
                          {item.kind === 'customer' && item.blocked_reason && !transferBusy &&
                            reviewDisputeLink(item.business_id, transferPreview.handover.id, transferPreview.takeover.id)}
                          {/* 逐项调整接手人：不选就是跟统一接管人 */}
                          {canExecuteTransfer && !item.blocked_reason &&
                            (item.kind !== 'wecom_relation' || readinessQuery.data?.can_sync_external) && (
                            <Select
                              size="small"
                              value={transferAssignees[key]}
                              aria-label={`${item.label}的接手人`}
                              placeholder={`由 ${transferPreview.takeover.name} 接手`}
                              showClear
                              filter={optionMatcher}
                              onChange={(value) =>
                                setTransferAssignees((prev) => {
                                  const next = { ...prev }
                                  if (value === undefined || value === null) delete next[key]
                                  else next[key] = value as number
                                  return next
                                })
                              }
                              optionList={takeoverPeople.map((user) => ({ value: user.id, label: user.name }))}
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
                    这些客户的归属和企微关系暂不转交。裁定后重新查看交接清单。
                  </span>
                }
              />
            )}
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
              {canExecuteTransfer && eligibleTransferCount > 0 && '需要单独指定接手人的事项，可在对应行选择同事。'}
            </div>
          </div>
        )}

        {transferStage === 'result' && transferJob && (
          <div className="wecom-transfer-results">
            <div className="wecom-transfer-note">
              {typeof transferJob.detail?.handover === 'string' && typeof transferJob.detail?.takeover === 'string' && (
                <div><strong>{transferJob.detail.handover} → {transferJob.detail.takeover}</strong></div>
              )}
              <div className="wecom-transfer-result-status">
                <span>任务 #{transferJob.id} · 成功 {transferJob.success_count} 项 · 失败 {transferJob.fail_count} 项</span>
                <Tag size="small" color={JOB_STATUS_TONE[transferJob.status] ?? 'grey'}>{transferJob.status_label}</Tag>
              </div>
              {transferPendingQuery.isSuccess && (
                <div>
                  {transferOpenTotal ? `还有 ${transferOpenTotal} 项可以重试。` : '没有需要重试的项。'}
                  {transferOpenTotal > 0 && !readiness?.transfer_enabled && ' 暂时无法重试，请联系管理员核对交接设置。'}
                </div>
              )}
            </div>
            <div className="wecom-transfer-result-status">
              <span>查看</span>
              <Select
                aria-label="筛选交接事项"
                size="small"
                value={transferResultFilter}
                onChange={(value) => {
                  setTransferResultFilter(value as typeof transferResultFilter)
                  setTransferResultPage(1)
                }}
                optionList={[
                  { value: 'all', label: '全部事项' },
                  { value: 'pending', label: '可重试' },
                  { value: 'frozen', label: '已冻结' },
                ]}
              />
            </div>
            {(transferItemsQuery.isError || transferPendingQuery.isError) ? (
              <Banner type="warning" closeIcon={null} description={
                <div>暂时无法读取逐项结果。<Button size="small" onClick={() => {
                  void queryClient.invalidateQueries({ queryKey: transferResultQueryKey })
                }}>重新读取结果</Button></div>
              } />
            ) : transferItemsQuery.isPending || transferPendingQuery.isPending ? (
              <div role="status">正在读取交接结果…</div>
            ) : transferItemTotal === 0 ? (
              <Empty description={transferResultFilter === 'all' ? '这次交接没有逐项记录' : '没有符合筛选的事项'} />
            ) : (
              <>
                <ul className="wecom-transfer-result-list">
                  {transferItemsQuery.data.items.map((item) => (
                    <TransferResultItem key={item.id} item={item} reviewLink={
                      item.crm_status === 'frozen' && item.kind === 'customer' && item.business_id && !transferBusy
                        ? reviewDisputeLink(item.business_id, transferJob.detail?.handover_id, transferJob.detail?.takeover_id)
                        : undefined
                    } />
                  ))}
                </ul>
                {transferItemTotal > TRANSFER_RESULT_PAGE_SIZE && (
                  <div className="wecom-transfer-pagination">
                    <Button size="small" disabled={transferResultPage === 1} onClick={() => setTransferResultPage(transferResultPage - 1)}>上一页</Button>
                    <span>第 {transferResultPage} 页 / 共 {Math.ceil(transferItemTotal / TRANSFER_RESULT_PAGE_SIZE)} 页</span>
                    <Button size="small" disabled={transferResultPage * TRANSFER_RESULT_PAGE_SIZE >= transferItemTotal} onClick={() => setTransferResultPage(transferResultPage + 1)}>下一页</Button>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </Modal>
    </div>
  )
}
