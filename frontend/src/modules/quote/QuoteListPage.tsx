import { useRef, useState } from 'react'
import PageHeader from '../../shared/components/PageHeader'
import { formatMoney } from '../../shared/components/money'
import SectionCard from '../../shared/components/SectionCard'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { emptyText } from '../../shared/hooks/emptyText'
import { Button, Input, Modal, Select, Table, Toast } from '@douyinfe/semi-ui'

import { listOpportunities } from '../../shared/api/opportunity'
import { createQuote, listQuotes, type Quote } from '../../shared/api/quote'
import { newRequestKey } from '../../shared/api/requestKey'
import { usePermissions } from '../../shared/hooks/permissions'
import type { TagTone } from '../../shared/types'
import { optionMatcher, optionsWithCodeOnDuplicate } from '../../shared/components/optionMatch'
import StatusDot from '../../shared/components/StatusDot'

const STATUS_OPTIONS = [
  { value: 'draft', label: '草稿' },
  { value: 'pending_approval', label: '待审批' },
  { value: 'approved', label: '已通过' },
  { value: 'sent', label: '已发送' },
  { value: 'accepted', label: '已接受' },
  { value: 'declined', label: '客户拒绝' },
  { value: 'approval_rejected', label: '审批未通过' },
]

const STATUS_TONE: Record<string, TagTone> = {
  draft: 'grey',
  pending_approval: 'orange',
  approved: 'blue',
  sent: 'cyan',
  accepted: 'green',
  declined: 'red',
  approval_rejected: 'red',
}

export default function QuoteListPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { can } = usePermissions()

  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState<string | undefined>()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [createVisible, setCreateVisible] = useState(false)
  const [opportunityId, setOpportunityId] = useState<number | null>(null)
  // 一次"新建报价"意图一把键（第八批 8.15）：在 mutationFn 里现生成的话，
  // 弱网重试会换一把新键，服务端仍会建出第二条报价 —— 幂等就白做了。
  const createRequestKeyRef = useRef('')

  const query = useQuery({
    queryKey: ['quotes', { keyword, status, page, pageSize }],
    queryFn: () => listQuotes({ keyword, status, page, page_size: pageSize }),
  })
  const opportunitiesQuery = useQuery({
    queryKey: ['opportunities-for-quote'],
    queryFn: () => listOpportunities({ status: 'open', page_size: 100 }),
    enabled: createVisible,
  })

  const createMutation = useMutation({
    mutationFn: () =>
      createQuote({
        opportunity_id: opportunityId!,
        request_key: createRequestKeyRef.current,
      }),
    onSuccess: (data) => {
      // 缺少已维护售价的需求行不会自动写入报价；服务端会在 warnings 中提示人工定价。
      Toast.success('报价草稿已生成，请核对明细和定价提示')
      for (const warning of data.warnings ?? []) {
        Toast.warning({ content: warning, duration: 6 })
      }
      // §8.14 复审：主数据未确认的提醒由后端放在**另一个字段** `master_warnings`
      // 里（内容形如「ZX-6040 的「规格、单位」主数据尚未确认」）。原来前端只读
      // `warnings`，这条提示等于没接上 —— 用户看不到缺什么，直到发送被拒。
      for (const warning of data.master_warnings ?? []) {
        Toast.warning({ content: warning, duration: 8 })
      }
      // 这一把键已用完：再开一次表单会换成新键（两次真实报价必须是两条）
      createRequestKeyRef.current = ''
      setCreateVisible(false)
      setOpportunityId(null)
      void queryClient.invalidateQueries({ queryKey: ['quotes'] })
      navigate(`/quotes/${data.quote_id}`)
    },
    // 失败**不换键**：用户改完重提时服务端会释放占位，同一把键仍可重试
    onError: (error: Error) => Toast.error(error.message),
  })

  const columns = [
    {
      // 版本号并到单号下面一行小字：V1/V2 单独占一整列（100px 放两个字）太奢侈，
      // 而看单子又必须知道是第几版 —— 和单号放一起最省地方
      title: '报价单号',
      dataIndex: 'quote_no',
      width: 145,
      render: (text: string, record: Quote) => (
        <div>
          <Link to={`/quotes/${record.id}`} style={{ color: 'var(--crm-primary)' }}>
            {text}
          </Link>
          <div style={{ marginTop: 2, fontSize: 12, color: 'var(--crm-text-3)' }}>
            {record.current_version_no ? `第 ${record.current_version_no} 版` : '无版本'}
          </div>
        </div>
      ),
    },
    {
      // 商机名同理：并到客户名下面做小字，省掉一整个自适应列
      title: '客户 / 商机',
      dataIndex: 'customer_name',
      width: 175,
      render: (v: string | null, record: Quote) => (
        <div>
          <div>{v ?? '-'}</div>
          <div style={{ marginTop: 2, fontSize: 12, color: 'var(--crm-text-3)' }}>
            {record.opportunity_title ?? '未关联商机'}
          </div>
        </div>
      ),
    },
    {
      title: '报价总额',
      dataIndex: 'current_version_amount',
      width: 115,
      render: (v: number | null, record: Quote) =>
        formatMoney(record.current_version_amount_exact ?? v, record.currency),
    },
    {
      title: '报价状态',
      dataIndex: 'status_label',
      width: 100,
      render: (value: string, record: Quote) => (
        <StatusDot label={value} tone={(STATUS_TONE[record.status] ?? 'grey') as TagTone} />
      ),
    },
    {
      title: '审批',
      dataIndex: 'approval_status',
      width: 95,
      render: (value: string | null) =>
        value === 'approved' ? (
          <StatusDot label="已通过" tone="green" />
        ) : value === 'pending' ? (
          <StatusDot label="审批中" tone="orange" />
        ) : value === 'rejected' ? (
          <StatusDot label="被拒" tone="red" />
        ) : (
          <StatusDot label="未提交" tone="grey" />
        ),
    },
    {
      title: '有效期',
      dataIndex: 'valid_until',
      width: 105,
      render: (v: string | null) => v ?? '-',
    },
    { title: '负责人', dataIndex: 'owner_name', width: 90, render: (v: string | null) => v ?? '-' },
  ]

  return (
    <div className="page-container">
      <PageHeader
        title="报价中心"
        subtitle="从商机生成报价，拟报价默认取系统适用价（客户目标价仅作参考）；整单优惠后超出权限必须审批"
      />

      <SectionCard>
        <div className="toolbar">
          <Input
            placeholder="搜索报价单号"
            value={keywordInput}
            onChange={setKeywordInput}
            onEnterPress={() => {
              setKeyword(keywordInput.trim())
              setPage(1)
            }}
            style={{ width: 220 }}
            showClear
          />
          <Select
            placeholder="报价状态"
            value={status}
            onChange={(value) => {
              setStatus(value as string | undefined)
              setPage(1)
            }}
            optionList={STATUS_OPTIONS}
            style={{ width: 150 }}
            showClear
          />
          <Button
            onClick={() => {
              setKeyword(keywordInput.trim())
              setPage(1)
            }}
          >
            查询
          </Button>
          <div style={{ flex: 1 }} />
          {can('quote:manage') && (
            <Button
              theme="solid"
              onClick={() => {
                // 每次打开"新建报价"都是一次新意图 → 换一把新的幂等键
                createRequestKeyRef.current = newRequestKey()
                setCreateVisible(true)
              }}
            >
              新建报价
            </Button>
          )}
        </div>

        <Table<Quote>
          columns={columns}
          dataSource={query.data?.items ?? []}
          loading={query.isLoading}
          rowKey="id"
          size="middle"
          empty={emptyText(query, '还没有报价单')}
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
        title="新建报价"
        // 不写 width 时 Semi 用 448（比全站小表单还窄 72）
        width={520}
        visible={createVisible}
        onCancel={() => setCreateVisible(false)}
        onOk={() => {
          if (!opportunityId) {
            Toast.warning('请选择商机')
            return
          }
          createMutation.mutate()
        }}
        confirmLoading={createMutation.isPending}
        okText="生成报价单"
      >
        <div style={{ marginBottom: 8 }}>选择商机（明细取自该商机的需求商品）</div>
        <div style={{ marginBottom: 12, fontSize: 12, color: 'var(--crm-text-3)' }}>
          报价必须关联商机；还没有合适商机？去「查价」页选品下单，可一键新建快捷商机
        </div>
        <Select
          placeholder="选择商机"
          value={opportunityId ?? undefined}
          onChange={(value) => setOpportunityId(value as number)}
          optionList={optionsWithCodeOnDuplicate(
            opportunitiesQuery.data?.items ?? [],
            (item) => `${item.title}（${item.customer_name ?? ''}）`,
            (item) => item.id,
          )}
          filter={optionMatcher}
          loading={opportunitiesQuery.isLoading}
          style={{ width: '100%' }}
        />
      </Modal>
    </div>
  )
}
