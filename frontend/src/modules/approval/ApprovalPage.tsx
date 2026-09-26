import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Input, Modal, Table, Tabs, Tag, Toast } from '@douyinfe/semi-ui'

import {
  approveApproval,
  listApprovals,
  rejectApproval,
  type ApprovalRow,
} from '../../shared/api/quote'
import PageHeader from '../../shared/components/PageHeader'
import { usePermissions } from '../../shared/hooks/permissions'

const TABS = [
  { tab: '待我审批', itemKey: 'pending' },
  { tab: '我已提交', itemKey: 'mine' },
  { tab: '已通过', itemKey: 'approved' },
  { tab: '已拒绝', itemKey: 'rejected' },
]

export default function ApprovalPage() {
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const [activeKey, setActiveKey] = useState('pending')
  const [rejectTarget, setRejectTarget] = useState<ApprovalRow | null>(null)
  const [rejectComment, setRejectComment] = useState('')

  const query = useQuery({
    queryKey: ['approvals', activeKey],
    queryFn: () =>
      activeKey === 'mine'
        ? listApprovals({ mine: true, page_size: 50 })
        : listApprovals({ status: activeKey, page_size: 50 }),
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['approvals'] })
    void queryClient.invalidateQueries({ queryKey: ['quotes'] })
  }

  const approveMutation = useMutation({
    mutationFn: (id: number) => approveApproval(id),
    onSuccess: () => {
      Toast.success('已通过')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const rejectMutation = useMutation({
    mutationFn: () => rejectApproval(rejectTarget!.id, rejectComment),
    onSuccess: () => {
      Toast.success('已拒绝')
      setRejectTarget(null)
      setRejectComment('')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const columns = [
    { title: '报价单号', dataIndex: 'quote_no', width: 160, render: (v: string | null) => v ?? '-' },
    { title: '版本', dataIndex: 'version_no', width: 80, render: (v: number | null) => (v ? `V${v}` : '-') },
    { title: '客户', dataIndex: 'customer_name', width: 220, render: (v: string | null) => v ?? '-' },
    { title: '申请人', dataIndex: 'applicant_name', width: 100, render: (v: string | null) => v ?? '-' },
    {
      title: '报价总额',
      dataIndex: 'total_amount',
      width: 130,
      render: (v: number | null) => (v === null || v === undefined ? '-' : `¥${v.toLocaleString('zh-CN')}`),
    },
    {
      title: '超权限明细',
      render: (_: unknown, record: ApprovalRow) => {
        const offending = record.summary?.offending ?? []
        if (!offending.length) return '-'
        return (
          <div style={{ fontSize: 12, color: 'var(--crm-text-2)' }}>
            {offending.map((item) => (
              <div key={item.sku_code}>
                {item.sku_code}：报 ¥{item.quoted_price}，最低 ¥{item.minimum_price}，
                利润率 {(item.profit_rate * 100).toFixed(2)}%
              </div>
            ))}
          </div>
        )
      },
    },
    {
      title: '状态',
      dataIndex: 'status_label',
      width: 100,
      render: (value: string) => (
        <Tag color={value === '待审批' ? 'orange' : value === '已通过' ? 'green' : 'grey'}>{value}</Tag>
      ),
    },
    {
      title: '申请说明',
      width: 180,
      render: (_: unknown, record: ApprovalRow) => record.summary?.reason ?? '-',
    },
    {
      title: '操作',
      width: 180,
      render: (_: unknown, record: ApprovalRow) => (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          {record.version_id && (
            <Link to={`/quotes/${record.quote_id}?version=${record.version_id}`} style={{ color: 'var(--crm-primary)' }}>
              看报价
            </Link>
          )}
          {record.status === 'pending' && can('quote:approve') && (
            <>
              <a style={{ color: 'var(--crm-success)' }} onClick={() => approveMutation.mutate(record.id)}>
                通过
              </a>
              <a style={{ color: 'var(--crm-error)' }} onClick={() => setRejectTarget(record)}>
                拒绝
              </a>
            </>
          )}
        </div>
      ),
    },
  ]

  return (
    <div className="page-container">
      <PageHeader
        title="报价审批"
        subtitle="业务员报价低于自己的价格权限时，必须经审批才能对外发送；审批只看超出权限的部分"
      />

      <div className="card-block">
        <Tabs type="line" activeKey={activeKey} onChange={setActiveKey} tabList={TABS} />
        <div style={{ marginTop: 16 }}>
          <Table<ApprovalRow>
            columns={columns}
            dataSource={query.data?.items ?? []}
            loading={query.isLoading}
            rowKey="id"
            pagination={false}
            empty="没有待处理的审批"
            scroll={{ x: 1300 }}
          />
        </div>
      </div>

      <Modal
        title="拒绝报价"
        visible={Boolean(rejectTarget)}
        onCancel={() => setRejectTarget(null)}
        onOk={() => rejectMutation.mutate()}
        confirmLoading={rejectMutation.isPending}
        okText="确认拒绝"
      >
        <Input
          value={rejectComment}
          onChange={setRejectComment}
          placeholder="拒绝原因，例如：低于成本，不可让价"
        />
      </Modal>
    </div>
  )
}
