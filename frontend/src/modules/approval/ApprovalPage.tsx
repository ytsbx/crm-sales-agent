import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { emptyText } from '../../shared/hooks/emptyText'
import { Input, Modal, Popconfirm, Select, Table, Tabs, Tag, Toast } from '@douyinfe/semi-ui'

import {
  approveApproval,
  listApprovals,
  rejectApproval,
  transferApproval,
  type ApprovalRow,
} from '../../shared/api/quote'
import { listUsers } from '../../shared/api/system'
import PageHeader from '../../shared/components/PageHeader'
import { usePermissions } from '../../shared/hooks/permissions'
import SectionCard from '../../shared/components/SectionCard'
import RulesPanel from './RulesPanel'
import { optionMatcher } from '../../shared/components/optionMatch'

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
  const [page, setPage] = useState(1)
  const [rejectTarget, setRejectTarget] = useState<ApprovalRow | null>(null)
  const [rejectComment, setRejectComment] = useState('')
  const [transferTarget, setTransferTarget] = useState<ApprovalRow | null>(null)
  const [transferUserId, setTransferUserId] = useState<number | null>(null)
  const [transferComment, setTransferComment] = useState('')

  // 规则配置只有管理员可见（后端写接口也是 settings:manage 把门）
  const TABS_WITH_RULES = [
    ...TABS,
    ...(can('settings:manage') ? [{ tab: '规则配置', itemKey: 'rules' }] : []),
  ]

  const query = useQuery({
    queryKey: ['approvals', activeKey, page],
    queryFn: () =>
      activeKey === 'mine'
        ? listApprovals({ mine: true, status: '', page, page_size: 50 })
        : listApprovals({ status: activeKey, pending_for_me: activeKey === 'pending', page, page_size: 50 }),
    enabled: activeKey !== 'rules',
  })

  // 转交接收人列表：打开始才拉（有 customer:assign / user:manage 权限的人能看到）
  const usersQuery = useQuery({
    queryKey: ['transfer-users'],
    queryFn: () => listUsers({ page_size: 100 }),
    enabled: Boolean(transferTarget),
  })

  const refresh = () => {
    setPage(1)
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

  const transferMutation = useMutation({
    mutationFn: () => transferApproval(transferTarget!.id, transferUserId!, transferComment || undefined),
    onSuccess: () => {
      Toast.success('已转交')
      setTransferTarget(null)
      setTransferUserId(null)
      setTransferComment('')
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
      width: 120,
      render: (value: string, record: ApprovalRow) => {
        // 规则加签的单子：本级通过后停在会签节点，要让人一眼看出现在轮到谁
        const coSign = record.current_node === 'co_sign' ? record.summary?.co_sign : null
        return (
          <div style={{ display: 'grid', gap: 2 }}>
            <Tag color={value === '待审批' ? 'orange' : value === '已通过' ? 'green' : 'grey'}>{value}</Tag>
            {coSign && (
              <Tag color="red" size="small">
                待{coSign.label ?? '会签'}
              </Tag>
            )}
            {record.summary?.auto_passed && <Tag color="green" size="small">规则免审</Tag>}
          </div>
        )
      },
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
          {record.can_approve && (
            <>
              {/* 审批通过是不可逆的状态推进（报价随即可以对客发送），点一下生效太轻，
                  必须二次确认。「拒绝」本来就走原因弹窗，那条不用加。 */}
              <Popconfirm
                title="通过这份报价的审批？"
                content="通过后报价即可对客发送，请确认金额与优惠幅度都核对过。"
                onConfirm={() => approveMutation.mutate(record.id)}
              >
                <a style={{ color: 'var(--crm-success)' }}>通过</a>
              </Popconfirm>
              <a style={{ color: 'var(--crm-error)' }} onClick={() => setRejectTarget(record)}>
                拒绝
              </a>
              {can('quote:approve') && <a
                style={{ color: 'var(--crm-text-2)' }}
                onClick={() => {
                  setTransferTarget(record)
                  setTransferUserId(null)
                  setTransferComment('')
                }}
              >
                转交
              </a>}
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

      <SectionCard>
        <Tabs type="line" activeKey={activeKey} onChange={(key) => {
          setActiveKey(key)
          setPage(1)
        }} tabList={TABS_WITH_RULES} />
        {activeKey === 'rules' ? (
          <div style={{ marginTop: 16 }}>
            <RulesPanel />
          </div>
        ) : (
          <div style={{ marginTop: 16 }}>
            <Table<ApprovalRow>
              columns={columns}
              dataSource={query.data?.items ?? []}
              loading={query.isLoading}
              rowKey="id"
              pagination={{
                currentPage: page,
                pageSize: 50,
                total: query.data?.total ?? 0,
                onPageChange: setPage,
              }}
              empty={emptyText(query, '没有待处理的审批')}
              scroll={{ x: 1300 }}
            />
          </div>
        )}
      </SectionCard>

      <Modal
        title={`转交审批：${transferTarget?.quote_no ?? ''}`}
        visible={Boolean(transferTarget)}
        onCancel={() => setTransferTarget(null)}
        onOk={() => {
          if (!transferUserId) {
            Toast.warning('请选择接收人')
            return
          }
          transferMutation.mutate()
        }}
        confirmLoading={transferMutation.isPending}
        okText="确认转交"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <Select
            placeholder="选择有资格处理当前审批的接收人"
            filter={optionMatcher}
            style={{ width: '100%' }}
            value={transferUserId}
            onChange={(v) => setTransferUserId(v as number)}
            optionList={(usersQuery.data?.items ?? []).map((u) => ({
              value: u.id,
              label: `${u.name}（${(u.roles ?? []).map((r) => r.name).join('、') || '无角色'}）`,
            }))}
          />
          <Input
            value={transferComment}
            onChange={setTransferComment}
            placeholder="转交说明（可选），例如：我在出差，麻烦处理"
          />
        </div>
      </Modal>

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
