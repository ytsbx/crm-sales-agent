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
import { listApprovalTransferCandidates } from '../../shared/api/approval'
import PageHeader from '../../shared/components/PageHeader'
import { usePermissions } from '../../shared/hooks/permissions'
import SectionCard from '../../shared/components/SectionCard'
import StatusDot from '../../shared/components/StatusDot'
import RulesPanel from './RulesPanel'
import { optionMatcher } from '../../shared/components/optionMatch'
import { formatMoney, toTwoDecimals } from '../../shared/components/money'

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
  const [pageSize, setPageSize] = useState(50)
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
    queryKey: ['approvals', activeKey, page, pageSize],
    queryFn: () =>
      activeKey === 'mine'
        ? listApprovals({ mine: true, status: '', page, page_size: pageSize })
        : listApprovals({ status: activeKey, pending_for_me: activeKey === 'pending', page, page_size: pageSize }),
    enabled: activeKey !== 'rules',
  })

  // 当前审批的合格接收人列表；不借用用户管理/分配客户权限。
  const usersQuery = useQuery({
    queryKey: ['approval-transfer-users', transferTarget?.id],
    queryFn: () => listApprovalTransferCandidates(transferTarget!.id),
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
    /* 「版本」原来单独一列只放一个「V1」。并进单号下面一行小字，
       九列变八列，表格才收得进内容区。 */
    {
      title: '报价单号',
      dataIndex: 'quote_no',
      /* 175 放不下「箭头 + 单号 + 版本」一整行，版本被挤成「第 …」；190 刚好 */
      width: 190,
      /* 原来单号与「第 N 版」是上下两行的块级 div —— 左边那个展开箭头是 inline 的，
         块级内容一定另起一行，于是箭头独占一行、单号再一行、版本第三行。
         改成同一行的两个 inline 片段：箭头 + 单号 + 小字版本，整行只占一行高。 */
      render: (v: string | null, record: ApprovalRow) => (
        <span>
          {v ?? '-'}
          {record.version_no ? (
            <span style={{ fontSize: 12, color: 'var(--crm-text-3)', marginLeft: 6 }}>
              V{record.version_no}
            </span>
          ) : null}
        </span>
      ),
    },
    { title: '客户', dataIndex: 'customer_name', width: 215, ellipsis: true, render: (v: string | null) => v ?? '-' },
    { title: '申请人', dataIndex: 'applicant_name', width: 110, render: (v: string | null) => v ?? '-' },
    {
      title: '报价总额',
      dataIndex: 'total_amount',
      width: 150,
      // 报价总额是业务单据上的钱，按全站口径必须两位小数（¥84,030.00），
      // 不能像统计卡那样取整。
      render: (v: number | null) => (v === null || v === undefined ? '-' : formatMoney(toTwoDecimals(v), 'CNY')),
    },
    {
      title: '状态',
      dataIndex: 'status_label',
      width: 155,
      render: (value: string, record: ApprovalRow) => {
        // 规则加签的单子：本级通过后停在会签节点，要让人一眼看出现在轮到谁
        const coSign = record.current_node === 'co_sign' ? record.summary?.co_sign : null
        return (
          // 单元格里是块级 grid：全站表头/内容居中，块级内容要显式居中，
          // 否则「状态」两个字在列中间、「● 待审批」贴着列左边起，上下错半格。
          <div style={{ display: 'grid', gap: 2, justifyItems: 'center' }}>
            <StatusDot
              label={String(value)}
              tone={value === '待审批' ? 'orange' : value === '已通过' ? 'green' : 'grey'}
            />
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
      title: '操作',
      width: 215,
      // 「看报价 通过 拒绝 转交」四个动作挤在 10px 间距里，字号又小，读起来是
      // 一串没断开的字。间距拉到 16，四个动作才分得开（2026-11-16）。
      render: (_: unknown, record: ApprovalRow) => (
        <div
          style={{
            display: 'flex',
            gap: 16,
            flexWrap: 'wrap',
            alignItems: 'center',
            // 同上：flex 容器默认靠左，补 justifyContent 才和表头「操作」同一条中线
            justifyContent: 'center',
          }}
        >
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
              {(can('quote:approve') || can('quote:finance_approve')) && <a
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

      {/* 待审批常常只有一两条：卡按内容收住，不拉满整屏 */}
      <SectionCard
        className={
          activeKey !== 'rules' && (query.data?.items?.length ?? 0) <= 8 ? 'auto-height' : undefined
        }
      >
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
              /* 默认列宽按内容算：明细里一条长说明就能把整张表顶宽。
                 固定列宽布局后，宽度完全按 columns 里写的 width 分配。 */
              tableLayout="fixed"
              className="approval-table"
              columns={columns}
              /* 「申请说明 / 超权限」那一列原来把整张表顶到 1440，而内容区只有 1130，
                 右边一直被裁。它本质是「一行一段说明」，不是能横向对照的字段，
                 挪进点行展开：列表留六个对照列，想细看的点左边箭头。
                 信息一点没丢。 */
              // 同上：小三角换成文字，减少"这行能点吗"的犹豫
              expandIcon={(expanded?: boolean) => (
                <span className="expand-toggle">{expanded ? '收起' : '展开'}</span>
              )}
              expandedRowRender={(record?: ApprovalRow) => {
                if (!record) return null
                const reason = record.summary?.reason
                const offending = record.summary?.offending ?? []
                if (!reason && offending.length === 0) return null
                return (
                  <div style={{ display: 'grid', gap: 6, padding: '4px 10px', textAlign: 'left' }}>
                    {reason && (
                      <div style={{ fontSize: 13, lineHeight: 1.7 }}>
                        <span style={{ color: 'var(--crm-text-3)' }}>申请说明：</span>
                        {reason}
                      </div>
                    )}
                    {offending.map((item) => (
                      <div key={item.sku_code} style={{ fontSize: 13, lineHeight: 1.7 }}>
                        <span style={{ color: 'var(--crm-text-3)' }}>超权限：</span>
                        {item.sku_code}
                        {' —— '}
                        {item.reason ??
                          `报 ¥${item.quoted_price ?? '-'}，最低 ¥${item.minimum_price ?? '-'}，利润率 ${
                            item.profit_rate == null ? '未评估' : `${(item.profit_rate * 100).toFixed(2)}%`
                          }`}
                      </div>
                    ))}
                  </div>
                )
              }}
              rowExpandable={(record?: ApprovalRow) =>
                Boolean(record?.summary?.reason) || (record?.summary?.offending ?? []).length > 0
              }
              dataSource={query.data?.items ?? []}
              loading={query.isLoading}
              rowKey="id"
              pagination={{
                currentPage: page,
                pageSize,
                total: query.data?.total ?? 0,
                onPageChange: setPage,
                showSizeChanger: true,
                pageSizeOpts: [10, 20, 50, 100],
                onPageSizeChange: (size: number) => { setPageSize(size); setPage(1) },
              }}
              empty={emptyText(query, '没有待处理的审批')}
              scroll={{ x: '100%' }}
            />
          </div>
        )}
      </SectionCard>

      <Modal
        width={520}
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
            optionList={(usersQuery.data ?? []).map((u) => ({
              value: u.id,
              label: u.name,
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
        width={520}
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
