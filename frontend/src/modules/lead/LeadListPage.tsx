import { useEffect, useState } from 'react'
import { parseOptionalNumber } from '../../shared/number-input'
import MaskedContact from '../../shared/components/MaskedContact'
import PageHeader from '../../shared/components/PageHeader'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { emptyNode } from '../../shared/components/EmptyState'
import {
  Button,
  Checkbox,
  DatePicker,
  Input,
  Modal,
  Popconfirm,
  Radio,
  RadioGroup,
  Select,
  Table,
  TextArea,
  Toast,
} from '@douyinfe/semi-ui'

import {
  assignLead,
  claimLead,
  convertLead,
  createLead,
  deduplicateLead,
  discardLead,
  listLeads,
  releaseLead,
  type DuplicateCandidate,
  type Lead,
} from '../../shared/api/lead'
import { listUsers } from '../../shared/api/system'
import { optionMatcher } from '../../shared/components/optionMatch'
import FollowUpModal from '../common/FollowUpModal'
import { usePermissions } from '../../shared/hooks/permissions'
import type { TagTone } from '../../shared/types'
import SectionCard from '../../shared/components/SectionCard'
import StatusDot from '../../shared/components/StatusDot'

const STATUS_OPTIONS = [
  { value: 'pending', label: '待分配' },
  { value: 'assigned', label: '已分配' },
  { value: 'following', label: '跟进中' },
  { value: 'converted', label: '已转客户' },
  { value: 'invalid', label: '无效' },
]

const SOURCE_OPTIONS = ['展会', '官网', '企业微信', '老客户介绍', 'Excel 导入', '手工录入', '其他渠道'].map(
  (value) => ({ value, label: value }),
)

const STATUS_COLOR: Record<string, TagTone> = {
  pending: 'orange',
  assigned: 'blue',
  following: 'green',
  converted: 'violet',
  invalid: 'grey',
}

const EMPTY_FORM = {
  name: '',
  company_name: '',
  contact_name: '',
  mobile: '',
  email: '',
  source: '手工录入',
  source_detail: '',
  region: '',
  remark: '',
}

export default function LeadListPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { can } = usePermissions()

  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState<string | undefined>()
  const [onlyUnassigned, setOnlyUnassigned] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  // 工作台右上「新建线索」跳到 /leads?new=1 时，直接把新建弹窗打开 ——
  // 否则用户点完只看到一张列表，还得自己再找一次按钮（2026-11-17 视觉审视 D2-04）。
  const openCreateFromUrl = typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('new') === '1'
  const [createVisible, setCreateVisible] = useState(openCreateFromUrl)
  const [form, setForm] = useState({ ...EMPTY_FORM })

  const [assignTarget, setAssignTarget] = useState<Lead | null>(null)
  const [assignTo, setAssignTo] = useState<number | null>(null)
  const [discardTarget, setDiscardTarget] = useState<Lead | null>(null)
  const [discardReason, setDiscardReason] = useState('')
  const [followupTarget, setFollowupTarget] = useState<Lead | null>(null)

  const [convertTarget, setConvertTarget] = useState<Lead | null>(null)
  const [customerMode, setCustomerMode] = useState<'new' | 'existing'>('new')
  const [existingCustomerId, setExistingCustomerId] = useState<number | null>(null)
  const [createOpportunity, setCreateOpportunity] = useState(false)
  const [opportunityTitle, setOpportunityTitle] = useState('')
  const [expectedAmount, setExpectedAmount] = useState('')
  const [expectedCloseDate, setExpectedCloseDate] = useState<Date | null>(null)

  const query = useQuery({
    queryKey: ['leads', { keyword, status, onlyUnassigned, page, pageSize }],
    queryFn: () =>
      listLeads({
        keyword,
        status,
        unassigned: onlyUnassigned || undefined,
        page,
        page_size: pageSize,
      }),
  })

  const usersQuery = useQuery({
    queryKey: ['assignable-users'],
    queryFn: () => listUsers({ page: 1, page_size: 100 }),
    enabled: Boolean(assignTarget) && can('lead:assign'),
  })

  const candidatesQuery = useQuery({
    queryKey: ['lead-candidates', convertTarget?.id],
    queryFn: () => deduplicateLead(convertTarget!.id),
    enabled: Boolean(convertTarget),
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['leads'] })
    void queryClient.invalidateQueries({ queryKey: ['customers'] })
    void queryClient.invalidateQueries({ queryKey: ['opportunities'] })
  }

  const createMutation = useMutation({
    mutationFn: () => createLead(form),
    onSuccess: () => {
      Toast.success('线索已创建')
      setCreateVisible(false)
      setForm({ ...EMPTY_FORM })
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const claimMutation = useMutation({
    mutationFn: (id: number) => claimLead(id),
    onSuccess: () => {
      Toast.success('领取成功')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const assignMutation = useMutation({
    mutationFn: () => assignLead(assignTarget!.id, assignTo),
    onSuccess: () => {
      Toast.success('已分配')
      setAssignTarget(null)
      setAssignTo(null)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const releaseMutation = useMutation({
    mutationFn: (id: number) => releaseLead(id),
    onSuccess: () => {
      Toast.success('已释放回线索池')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const discardMutation = useMutation({
    mutationFn: () => discardLead(discardTarget!.id, discardReason.trim()),
    onSuccess: () => {
      Toast.success('线索已废弃')
      setDiscardTarget(null)
      setDiscardReason('')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const convertMutation = useMutation({
    mutationFn: () =>
      convertLead(convertTarget!.id, {
        customer_mode: customerMode,
        customer_id: customerMode === 'existing' ? existingCustomerId : null,
        create_contact: true,
        create_opportunity: createOpportunity,
        opportunity_title: createOpportunity ? opportunityTitle || null : null,
        // 不能直接 Number(x)：填「约5万」→ NaN → JSON null → 商机建出来但
        // 预计金额为空，用户只看到「线索已转化」，漏斗金额从此少一笔
        // （2026-10-14 审查修）。
        expected_amount: (() => {
          const v = parseOptionalNumber(expectedAmount)
          if (typeof v === 'number' && Number.isNaN(v)) {
            throw new Error('预计金额只能填数字（不要写单位）')
          }
          return v
        })(),
        expected_close_date: createOpportunity && expectedCloseDate
          ? `${expectedCloseDate.getFullYear()}-${String(expectedCloseDate.getMonth() + 1).padStart(2, '0')}-${String(expectedCloseDate.getDate()).padStart(2, '0')}`
          : null,
      }),
    onSuccess: (data) => {
      Toast.success('线索已转化')
      setConvertTarget(null)
      setCustomerMode('new')
      setExistingCustomerId(null)
      setCreateOpportunity(false)
      setOpportunityTitle('')
      setExpectedAmount('')
      setExpectedCloseDate(null)
      refresh()
      if (data.customer_id) navigate(`/customers/${data.customer_id}`)
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  /* 1024 宽内容区约 700px，线索表八列合计 1066px，「状态 / 负责人 / 操作」
     整块被裁到卡片外。窄屏切紧凑档：联系人 + 负责人并到线索名称第二行，
     其余列宽收紧，操作列改两列网格，五个文字链才排得下。 */
  const [compact, setCompact] = useState(() => window.innerWidth < 1200)
  useEffect(() => {
    const onResize = () => setCompact(window.innerWidth < 1200)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const columns = [
    // 名称列收窄并允许省略：9 列加起来 1350，比内容区还宽，表格内部多出一条横向滚动条。
    // 170 → 200：12 字的线索名（「天津线索30【演示造数】」）实测要 178，
    // 170 时有 5px 溢出（2026-11-16 全站表格扫描）。
    {
      title: '线索名称',
      dataIndex: 'name',
      width: compact ? 140 : 200,
      ellipsis: !compact,
      render: (text: string, record: Lead) =>
        compact ? (
          <div style={{ minWidth: 0 }}>
            <div
              title={text}
              style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
            >
              {text}
            </div>
            <div style={{ marginTop: 2, fontSize: 12, color: 'var(--crm-text-3)' }}>
              {(record.contact_name ?? '-')} · {(record.owner_name ?? '未分配')}
            </div>
          </div>
        ) : (
          text
        ),
    },
    {
      title: '公司',
      dataIndex: 'company_name',
      /* 130 → 150：企业名叫「青岛电子有限公司」这种七到九字是常态，
         130 下整列全是「天津电子有限公…」，一列省略号等于没有这一列。
         从操作列里让出来的空间（见下）补给这里。 */
      width: compact ? 100 : 150,
      ellipsis: true,
      render: (v: string | null) => v ?? '-',
    },
    ...(compact
      ? []
      : [{ title: '联系人', dataIndex: 'contact_name', width: 80, render: (v: string | null) => v ?? '-' }]),
    // 手机号 11 位：130（2026-11-19 复测）—— 118 时公司列刚加到 150、操作列加到 240，
    // 整表被压，11 位数字又截成「138269363…」。130 减掉单元格左右 32 的内边距，
    // 剩 98 刚好装下 11 位等宽数字。
    { title: '手机', dataIndex: 'mobile', width: compact ? 126 : 130, render: (v: string | null) => <MaskedContact value={v} /> },
    /* 「来源」和「地区」各占一列、内容都只有两三个字。并成一列「来源 · 地区」：
       两列 190 变一列 150，信息一点没少，正好把表格从 1150 收回到内容区里。 */
    {
      title: compact ? '来源' : '来源 · 地区',
      dataIndex: 'source',
      /* 126 是实测的刚好值（2026-11-19）：内容是「客户介绍 · 华东」这种 4+3 字
         外加两个空格，约 103px，再减掉单元格左右 32 的内边距 —— 120 会截成
         「客户介绍 · 华…」，136 才完整。多出来的 16px 从名称列（弹性）让。 */
      width: compact ? 84 : 136,
      render: (_: string | null, record: Lead) =>
        [record.source, record.region].filter((v) => v && v !== '-').join(' · ') || '-',
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: compact ? 96 : 96,
      render: (value: string, record: Lead) => (
        <StatusDot label={record.status_label} tone={(STATUS_COLOR[value] ?? 'grey') as TagTone} />
      ),
    },
    ...(compact
      ? []
      : [{
          title: '负责人',
          dataIndex: 'owner_name',
          width: 100,
          render: (v: string | null) => v ?? '未分配',
        }]),
    {
      title: '操作',
      // 操作最多同时出现五个文字链（领取/跟进/分配/转客户/释放/废弃里取五）。
      // 220 那会儿总宽 1140 > 内容区 1100：表格先挂横滚条，操作列又被压窄，
      // 于是「释放/废弃」折到第二行、行高一格一格不一样。
      // 现在把 公司 150→130、联系人 90→80、来源 150→110、状态 100→96 让出 74px，
      // 操作列仍是 220，五个链接一行放完、横滚条也不出现了（合计 1066 < 内容区 1070）。
      /* 220 → 240、链接间距 8 → 12：五个动作词（跟进 / 分配 / 转客户 / 释放 / 废弃）
         挤在 8px 里读成一串，"转客户"和"释放"尤其容易看混（2026-11-19 巡检）。 */
      width: compact ? 154 : 240,
      render: (_: unknown, record: Lead) => (
        <div
          style={
            compact
              ? {
                  /* 2 列 → 3 列（2026-11-19）：五个动作在 2 列里排成 2+2+1，
                     "废弃"孤零零占第三行，那一行的行高比别人高出一截；
                     改成 3 列正好两行（3+2），行高一致。154 宽放三列每列 51，
                     最长的是「转客户」三字（36px），够。 */
                  display: 'grid',
                  gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
                  gap: '2px 6px',
                  fontSize: 12,
                  lineHeight: 1.7,
                  whiteSpace: 'nowrap',
                }
              : { display: 'flex', gap: 12, flexWrap: 'nowrap', whiteSpace: 'nowrap', fontSize: 13 }
          }
        >
          {!record.owner_id && (
            <Popconfirm
              title={`确认领取线索「${record.name}」？`}
              content="领取后你将成为这条线索的负责人。"
              onConfirm={() => claimMutation.mutate(record.id)}
              disabled={claimMutation.isPending}
            >
              <a style={{ color: 'var(--crm-primary)' }}>{claimMutation.isPending ? '领取中…' : '领取'}</a>
            </Popconfirm>
          )}
          <a style={{ color: 'var(--crm-primary)' }} onClick={() => setFollowupTarget(record)}>
            跟进
          </a>
          {can('lead:assign') && (
            <a
              style={{ color: 'var(--crm-primary)' }}
              onClick={() => {
                setAssignTarget(record)
                setAssignTo(record.owner_id ?? null)
              }}
            >
              分配
            </a>
          )}
          {record.status !== 'converted' && can('lead:convert') && (
            <a style={{ color: 'var(--crm-primary)' }} onClick={() => {
              setOpportunityTitle('')
              setExpectedAmount('')
              setExpectedCloseDate(null)
              setConvertTarget(record)
            }}>
              转客户
            </a>
          )}
          {record.owner_id && can('lead:assign') && (
            /* 释放会丢掉归属（线索回到池子里、别人可以领走），点一下生效太轻。
               「领取」故意不加确认：抢线索本来就图快，加一步反而添麻烦
               （主人 2026-10-06 定：只给会丢归属/不可逆的动作加确认）。 */
            <Popconfirm
              title="释放这条线索回线索池？"
              content="释放后你不再是负责人，其他人可以领取。"
              onConfirm={() => releaseMutation.mutate(record.id)}
            >
              <a style={{ color: 'var(--crm-primary)' }}>释放</a>
            </Popconfirm>
          )}
          {can('lead:assign') && (
            <Popconfirm title="确认废弃这条线索？" onConfirm={() => setDiscardTarget(record)}>
              <a style={{ color: 'var(--crm-error)' }}>废弃</a>
            </Popconfirm>
          )}
        </div>
      ),
    },
  ]

  return (
    <div className="page-container">
      <PageHeader title="线索中心" subtitle="线索独立存在，转化时才产生客户、联系人和商机" />

      <SectionCard>
        <div className="toolbar">
          <Input
            placeholder="搜索线索 / 公司 / 联系人 / 手机"
            value={keywordInput}
            onChange={setKeywordInput}
            onEnterPress={() => {
              setKeyword(keywordInput.trim())
              setPage(1)
            }}
            style={{ width: 260 }}
            showClear
          />
          <Select
            placeholder="状态"
            value={status}
            onChange={(value) => {
              setStatus(value as string | undefined)
              setPage(1)
            }}
            optionList={STATUS_OPTIONS}
            style={{ width: 130 }}
            showClear
          />
          <Checkbox
            checked={onlyUnassigned}
            onChange={(event) => {
              setOnlyUnassigned(Boolean(event.target.checked))
              setPage(1)
            }}
          >
            只看线索池
          </Checkbox>
          <Button
            onClick={() => {
              setKeyword(keywordInput.trim())
              setPage(1)
            }}
          >
            查询
          </Button>
          <div style={{ flex: 1 }} />
          {can('lead:create') && (
            <Button type="primary" theme="outline" onClick={() => setCreateVisible(true)}>
              新建线索
            </Button>
          )}
        </div>

        <Table<Lead>
          className="leads-table"
          columns={columns}
          dataSource={query.data?.items ?? []}
          loading={query.isLoading}
          rowKey="id"
          size="middle"
          empty={emptyNode(query, {
            title: '还没有线索',
            hint: '点右上角「新建线索」记下第一条；线索转化时才会产生客户、联系人和商机。',
          })}
          pagination={{
            currentPage: page,
            pageSize,
            total: query.data?.total ?? 0,
            showSizeChanger: true,
            onPageChange: (next: number) => setPage(next),
            onPageSizeChange: (size: number) => {
              setPageSize(size)
              setPage(1)
            },
          }}
        />
      </SectionCard>

      <Modal
        title={
          <div className="modal-head">
            <span className="modal-head-icon" aria-hidden>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"><path d="M5 21V4" /><path d="M5 4h10l-1.6 3L15 10H5" /><path d="M9 14h4v7h-4z" /></svg>
            </span>
            <div>
              新建线索
              <div className="modal-subtitle">名称必填；公司、联系人可以后补</div>
            </div>
          </div>
        }
        width={600}
        visible={createVisible}
        onCancel={() => setCreateVisible(false)}
        onOk={() => {
          if (!form.name.trim()) {
            Toast.warning('线索名称必填')
            return
          }
          if (form.source === '其他渠道' && !form.source_detail.trim()) {
            Toast.warning('请填写其他渠道说明')
            return
          }
          createMutation.mutate()
        }}
        confirmLoading={createMutation.isPending}
        okText="创建"
      >
                <div className="form-cards">
          <section className="form-card">
            <div className="form-card-title">这条线索</div>
            <div className="form-card-grid">
              <div className="full">
                <div className="form-field-label">线索名称<span className="req">*</span></div>
                <Input
                  value={form.name}
                  onChange={(v) => setForm({ ...form, name: v })}
                  placeholder="客户要什么，例如：杭州电商仓周转箱需求"
                />
              </div>
              <div className="full">
                <div className="form-field-label">公司名称</div>
                <Input
                  value={form.company_name}
                  onChange={(v) => setForm({ ...form, company_name: v })}
                  placeholder="还没问到就先空着"
                />
              </div>
              <div className="full">
                <div className="form-field-label">备注</div>
                <TextArea
                  value={form.remark}
                  onChange={(v) => setForm({ ...form, remark: v })}
                  rows={2}
                  placeholder="需求细节、预算、时间点……"
                />
              </div>
            </div>
          </section>

          <section className="form-card">
            <div className="form-card-title">从哪来、找谁</div>
            <div className="form-card-grid">
              <div>
                <div className="form-field-label">来源</div>
                <Select
                  value={form.source}
                  onChange={(v) => setForm({
                    ...form,
                    source: v as string,
                    source_detail: v === '其他渠道' ? form.source_detail : '',
                  })}
                  optionList={SOURCE_OPTIONS}
                  style={{ width: '100%' }}
                />
              </div>
              <div>
                <div className="form-field-label">省份 / 地区</div>
                <Input value={form.region} onChange={(v) => setForm({ ...form, region: v })} placeholder="浙江" />
              </div>
              {form.source === '其他渠道' && (
                <div className="full">
                  <div className="form-field-label">渠道说明<span className="req">*</span></div>
                  <Input
                    value={form.source_detail}
                    onChange={(v) => setForm({ ...form, source_detail: v })}
                    placeholder="请填写具体来源，例如：合作伙伴推荐、线下拜访"
                  />
                </div>
              )}
              <div>
                <div className="form-field-label">联系人</div>
                <Input value={form.contact_name} onChange={(v) => setForm({ ...form, contact_name: v })} />
              </div>
              <div>
                <div className="form-field-label">手机</div>
                <Input value={form.mobile} onChange={(v) => setForm({ ...form, mobile: v })} />
              </div>
              <div className="full form-tip">
                联系人和手机可以先记个大概，后面进到客户中心再补全。
              </div>
            </div>
          </section>
        </div>
      </Modal>

      <Modal
        width={520}
        title={`分配线索：${assignTarget?.name ?? ''}`}
        visible={Boolean(assignTarget)}
        onCancel={() => setAssignTarget(null)}
        onOk={() => assignMutation.mutate()}
        confirmLoading={assignMutation.isPending}
        okText="分配"
      >
        <Select
          placeholder="选择负责人"
          value={assignTo ?? undefined}
          onChange={(value) => setAssignTo(value as number)}
          filter={optionMatcher}
          optionList={(usersQuery.data?.items ?? []).map((item) => ({
            value: item.id,
            label: `${item.name}（${item.department ?? '未分配部门'}）`,
          }))}
          style={{ width: '100%' }}
        />
      </Modal>

      <Modal
        width={520}
        title={`废弃线索：${discardTarget?.name ?? ''}`}
        visible={Boolean(discardTarget)}
        onCancel={() => setDiscardTarget(null)}
        onOk={() => {
          if (!discardReason.trim()) {
            Toast.warning('请填写废弃原因')
            return
          }
          discardMutation.mutate()
        }}
        confirmLoading={discardMutation.isPending}
        okText="确认废弃"
      >
        <Input
          value={discardReason}
          onChange={setDiscardReason}
          placeholder="例如：电话空号 / 无采购需求"
        />
      </Modal>

      <Modal
        title={`线索转客户：${convertTarget?.name ?? ''}`}
        visible={Boolean(convertTarget)}
        onCancel={() => {
          setConvertTarget(null)
          setExpectedCloseDate(null)
        }}
        onOk={() => {
          if (customerMode === 'existing' && !existingCustomerId) {
            Toast.warning('请选择要关联的客户')
            return
          }
          convertMutation.mutate()
        }}
        confirmLoading={convertMutation.isPending}
        okText="确认转化"
        width={520}
      >
        <div style={{ display: 'grid', gap: 16 }}>
          <div>
            <div style={{ marginBottom: 8 }}>客户处理方式</div>
            <RadioGroup
              value={customerMode}
              onChange={(event) => setCustomerMode(event.target.value as 'new' | 'existing')}
            >
              <Radio value="new">创建新客户</Radio>
              <Radio value="existing">关联已有客户</Radio>
            </RadioGroup>
          </div>

          {customerMode === 'existing' && (
            <div>
              <div style={{ marginBottom: 8, color: 'var(--crm-text-2)', fontSize: 13 }}>
                系统查重结果：{(candidatesQuery.data?.candidates ?? []).length} 个疑似客户
              </div>
              <Select
                placeholder="选择要关联的客户"
                value={existingCustomerId ?? undefined}
                onChange={(value) => setExistingCustomerId(value as number)}
                optionList={(candidatesQuery.data?.candidates ?? []).map((item: DuplicateCandidate) => ({
                  value: item.id,
                  label: `${item.name}（${item.region ?? '-'}，${item.level ?? '-'} 级）`,
                }))}
                loading={candidatesQuery.isLoading}
                style={{ width: '100%' }}
              />
            </div>
          )}

          <div>
            <Checkbox
              checked={createOpportunity}
              onChange={(event) => setCreateOpportunity(Boolean(event.target.checked))}
            >
              同时创建商机
            </Checkbox>
            {createOpportunity && (
              <div style={{ display: 'grid', gap: 8, marginTop: 8 }}>
                <Input
                  value={opportunityTitle}
                  onChange={setOpportunityTitle}
                  placeholder="商机名称，例如：周转箱年度采购"
                />
                <Input
                  value={expectedAmount}
                  onChange={setExpectedAmount}
                  placeholder="预计金额（元）"
                />
                <DatePicker
                  value={expectedCloseDate ?? undefined}
                  onChange={(date) => setExpectedCloseDate((date as Date) ?? null)}
                  placeholder="预计成交日期（选填）"
                  style={{ width: '100%' }}
                />
              </div>
            )}
          </div>

          <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
            转化会同时创建联系人（若线索有联系人信息），并把线索标记为「已转客户」，重复转化会被拦截。
          </div>
        </div>
      </Modal>

      <FollowUpModal
        visible={Boolean(followupTarget)}
        onClose={() => setFollowupTarget(null)}
        target={{ leadId: followupTarget?.id, customerId: followupTarget?.converted_customer_id ?? undefined }}
        onCreated={refresh}
      />
    </div>
  )
}
