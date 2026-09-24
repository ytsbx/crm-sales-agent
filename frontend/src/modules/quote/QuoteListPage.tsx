import { useState } from 'react'
import PageHeader from '../../shared/components/PageHeader'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Modal, Select, Table, Tag, Toast } from '@douyinfe/semi-ui'

import { listOpportunities } from '../../shared/api/opportunity'
import { createQuote, listQuotes, type Quote } from '../../shared/api/quote'
import { usePermissions } from '../../shared/hooks/permissions'
import type { TagTone } from '../../shared/types'

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
    mutationFn: () => createQuote({ opportunity_id: opportunityId! }),
    onSuccess: (data) => {
      Toast.success('报价单已生成，明细已按核价建议价带入')
      setCreateVisible(false)
      setOpportunityId(null)
      void queryClient.invalidateQueries({ queryKey: ['quotes'] })
      navigate(`/quotes/${data.quote_id}`)
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const columns = [
    {
      title: '报价单号',
      dataIndex: 'quote_no',
      width: 170,
      render: (text: string, record: Quote) => (
        <Link to={`/quotes/${record.id}`} style={{ color: 'var(--crm-primary)' }}>
          {text}
        </Link>
      ),
    },
    { title: '客户', dataIndex: 'customer_name', width: 230, render: (v: string | null) => v ?? '-' },
    { title: '商机', dataIndex: 'opportunity_title', render: (v: string | null) => v ?? '-' },
    {
      title: '当前版本',
      dataIndex: 'current_version_no',
      width: 100,
      render: (v: number | null) => (v ? `V${v}` : '-'),
    },
    {
      title: '报价总额',
      dataIndex: 'current_version_amount',
      width: 140,
      render: (v: number | null) =>
        v === null || v === undefined ? '-' : `¥${v.toLocaleString('zh-CN')}`,
    },
    {
      title: '报价状态',
      dataIndex: 'status_label',
      width: 120,
      render: (value: string, record: Quote) => (
        <Tag color={STATUS_TONE[record.status] ?? 'grey'}>{value}</Tag>
      ),
    },
    {
      title: '审批',
      dataIndex: 'approval_status',
      width: 110,
      render: (value: string | null) =>
        value === 'approved' ? (
          <Tag color="green">已通过</Tag>
        ) : value === 'pending' ? (
          <Tag color="orange">审批中</Tag>
        ) : value === 'rejected' ? (
          <Tag color="red">被拒</Tag>
        ) : (
          <Tag>未提交</Tag>
        ),
    },
    {
      title: '有效期',
      dataIndex: 'valid_until',
      width: 120,
      render: (v: string | null) => v ?? '-',
    },
    { title: '负责人', dataIndex: 'owner_name', width: 100, render: (v: string | null) => v ?? '-' },
  ]

  return (
    <div className="page-container">
      <PageHeader
        title="报价中心"
        subtitle="从商机生成报价，明细自动按核价建议价带入；超出权限的版本必须审批后才能发送"
      />

      <div className="card-block">
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
            <Button theme="solid" onClick={() => setCreateVisible(true)}>
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
          empty="还没有报价单"
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
        title="新建报价"
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
        <Select
          placeholder="选择商机"
          value={opportunityId ?? undefined}
          onChange={(value) => setOpportunityId(value as number)}
          optionList={(opportunitiesQuery.data?.items ?? []).map((item) => ({
            value: item.id,
            label: `${item.title}（${item.customer_name ?? ''}）`,
          }))}
          filter
          loading={opportunitiesQuery.isLoading}
          style={{ width: '100%' }}
        />
      </Modal>
    </div>
  )
}
