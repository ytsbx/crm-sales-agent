import { useState } from 'react'
import PageHeader from '../../shared/components/PageHeader'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  Checkbox,
  Input,
  Modal,
  Popconfirm,
  Radio,
  RadioGroup,
  Select,
  Table,
  Tag,
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
import FollowUpModal from '../common/FollowUpModal'
import { usePermissions } from '../../shared/hooks/permissions'
import type { TagTone } from '../../shared/types'

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
  const [pageSize, setPageSize] = useState(10)

  const [createVisible, setCreateVisible] = useState(false)
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
        expected_amount: expectedAmount ? Number(expectedAmount) : null,
      }),
    onSuccess: (data) => {
      Toast.success('线索已转化')
      setConvertTarget(null)
      setCustomerMode('new')
      setExistingCustomerId(null)
      setCreateOpportunity(false)
      setOpportunityTitle('')
      setExpectedAmount('')
      refresh()
      if (data.customer_id) navigate(`/customers/${data.customer_id}`)
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const columns = [
    { title: '线索名称', dataIndex: 'name', width: 220 },
    {
      title: '公司',
      dataIndex: 'company_name',
      width: 220,
      ellipsis: true,
      render: (v: string | null) => v ?? '-',
    },
    { title: '联系人', dataIndex: 'contact_name', width: 100, render: (v: string | null) => v ?? '-' },
    { title: '手机', dataIndex: 'mobile', width: 130, render: (v: string | null) => v ?? '-' },
    { title: '来源', dataIndex: 'source', width: 110, render: (v: string | null) => v ?? '-' },
    { title: '地区', dataIndex: 'region', width: 90, render: (v: string | null) => v ?? '-' },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (value: string, record: Lead) => (
        <Tag color={STATUS_COLOR[value] ?? 'grey'}>{record.status_label}</Tag>
      ),
    },
    {
      title: '负责人',
      dataIndex: 'owner_name',
      width: 100,
      render: (v: string | null) => v ?? '未分配',
    },
    {
      title: '操作',
      width: 280,
      render: (_: unknown, record: Lead) => (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          {!record.owner_id && (
            <a style={{ color: 'var(--crm-primary)' }} onClick={() => claimMutation.mutate(record.id)}>
              领取
            </a>
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
            <a style={{ color: 'var(--crm-primary)' }} onClick={() => setConvertTarget(record)}>
              转客户
            </a>
          )}
          {record.owner_id && can('lead:assign') && (
            <a style={{ color: 'var(--crm-primary)' }} onClick={() => releaseMutation.mutate(record.id)}>
              释放
            </a>
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

      <div className="card-block">
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
            <Button theme="solid" onClick={() => setCreateVisible(true)}>
              新建线索
            </Button>
          )}
        </div>

        <Table<Lead>
          columns={columns}
          dataSource={query.data?.items ?? []}
          loading={query.isLoading}
          rowKey="id"
          size="middle"
          empty="还没有线索"
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
      </div>

      <Modal
        title="新建线索"
        visible={createVisible}
        onCancel={() => setCreateVisible(false)}
        onOk={() => {
          if (!form.name.trim()) {
            Toast.warning('线索名称必填')
            return
          }
          createMutation.mutate()
        }}
        confirmLoading={createMutation.isPending}
        okText="创建"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <div style={{ marginBottom: 4 }}>线索名称 *</div>
            <Input
              value={form.name}
              onChange={(v) => setForm({ ...form, name: v })}
              placeholder="例如：杭州电商仓周转箱需求"
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>公司名称</div>
            <Input value={form.company_name} onChange={(v) => setForm({ ...form, company_name: v })} />
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>联系人</div>
              <Input value={form.contact_name} onChange={(v) => setForm({ ...form, contact_name: v })} />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>手机</div>
              <Input value={form.mobile} onChange={(v) => setForm({ ...form, mobile: v })} />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>来源</div>
              <Select
                value={form.source}
                onChange={(v) => setForm({ ...form, source: v as string })}
                optionList={SOURCE_OPTIONS}
                style={{ width: '100%' }}
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ marginBottom: 4 }}>省份 / 地区</div>
              <Input value={form.region} onChange={(v) => setForm({ ...form, region: v })} />
            </div>
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>备注</div>
            <TextArea value={form.remark} onChange={(v) => setForm({ ...form, remark: v })} rows={2} />
          </div>
        </div>
      </Modal>

      <Modal
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
          optionList={(usersQuery.data?.items ?? []).map((item) => ({
            value: item.id,
            label: `${item.name}（${item.department ?? '未分配部门'}）`,
          }))}
          style={{ width: '100%' }}
        />
      </Modal>

      <Modal
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
        onCancel={() => setConvertTarget(null)}
        onOk={() => {
          if (customerMode === 'existing' && !existingCustomerId) {
            Toast.warning('请选择要关联的客户')
            return
          }
          convertMutation.mutate()
        }}
        confirmLoading={convertMutation.isPending}
        okText="确认转化"
        width={560}
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
