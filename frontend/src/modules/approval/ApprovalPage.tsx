import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { emptyText } from '../../shared/hooks/emptyText'
import { Button, Input, Modal, Popconfirm, Select, Table, Tabs, Tag, Toast } from '@douyinfe/semi-ui'

import {
  approveApproval,
  listApprovals,
  rejectApproval,
  transferApproval,
  type ApprovalRow,
} from '../../shared/api/quote'
import { listApprovalTransferCandidates } from '../../shared/api/approval'
import PageHeader from '../../shared/components/PageHeader'
import HelpTip from '../../shared/components/HelpTip'
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

const APPROVAL_GUIDE: Record<string, { empty: string; next: string }> = {
  pending: { empty: '暂无待你处理的审批', next: '先点「看报价」核对当前版本，再决定通过、拒绝或转交。' },
  mine: { empty: '还没有你提交的报价审批', next: '在报价详情提交审批后，回这里看进度。' },
  approved: { empty: '暂无已通过的审批', next: '全部审批通过后，经办人可回报价详情发送给客户。' },
  rejected: { empty: '暂无已拒绝的审批', next: '点「看报价」查看对应版本和审批意见，按意见调整后再提交。' },
}

export default function ApprovalPage() {
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const [activeKey, setActiveKey] = useState('pending')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [rejectTarget, setRejectTarget] = useState<ApprovalRow | null>(null)
  // 展开行自己管：不再用 Semi 的 expandIcon（它会把按钮塞进首列、宽度不够时
  // 两个字被压成一字一行）。这里把按钮放进单独的 72px 小列，点击切换 key。
  const [expandedKeys, setExpandedKeys] = useState<number[]>([])
  const [rejectComment, setRejectComment] = useState('')
  const [transferTarget, setTransferTarget] = useState<ApprovalRow | null>(null)
  const [transferUserId, setTransferUserId] = useState<number | null>(null)
  const [transferComment, setTransferComment] = useState('')

  /* 1024 宽时内容区只剩约 700px，七个固定列合计 1007px，右侧「状态 / 操作」
     直接被推到卡片外，出现横向滚动条（2026-11-18 主人要求全站禁止横向滚动）。
     这里按窗口宽度切一档「紧凑列宽」：列宽整体收紧，并把「申请人」并进客户单元格
     的第二行小字——信息不丢，只是不再单独占一列。 */
  const [compact, setCompact] = useState(() => window.innerWidth < 1200)
  useEffect(() => {
    const onResize = () => setCompact(window.innerWidth < 1200)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

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
  const canConfirmTransfer = Boolean(
    usersQuery.isSuccess && usersQuery.data.some((person) => person.id === transferUserId),
  )

  const refresh = () => {
    setPage(1)
    void queryClient.invalidateQueries({ queryKey: ['approvals'] })
    void queryClient.invalidateQueries({ queryKey: ['quotes'] })
  }

  const approveMutation = useMutation({
    mutationFn: (id: number) => approveApproval(id),
    onSuccess: (data) => {
      Toast.success(data?.current_node === 'co_sign'
        ? '本级已通过，接下来等待会签。'
        : '审批已通过，经办人可回报价详情发送。')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const transferMutation = useMutation({
    mutationFn: () => transferApproval(transferTarget!.id, transferUserId!, transferComment || undefined),
    onSuccess: () => {
      Toast.success('已转交，由接收人继续处理当前审批。')
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
      Toast.success('已拒绝，申请人可以查看你的审批意见。')
      setRejectTarget(null)
      setRejectComment('')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const columns = [
    /* 展开按钮单独占最前面一列（64px，表头留空）。
       原来它挤在「报价单号」那一格的最前面：单号 + V1 已经把 190px 占满，
       「展开」两个字被压成一行一个字、竖着摞起来（2026-11-18 主人：
       "展开方式换一下，文字不要倒转"）。单独一列以后，文字永远横排。 */
    {
      title: '',
      dataIndex: '_expand',
      width: compact ? 72 : 72,
      // 展开 / 收起做成文字按钮：横排、永远一行，点一下切换这一行的展开状态
      render: (_: unknown, record: ApprovalRow) => {
        const open = expandedKeys.includes(record.id)
        return (
          <span
            className="expand-toggle"
            style={{ color: 'var(--crm-primary)', cursor: 'pointer' }}
            onClick={() => {
              setExpandedKeys((keys) =>
                open ? keys.filter((key) => key !== record.id) : [...keys, record.id],
              )
            }}
          >
            {open ? '收起' : '展开'}
          </span>
        )
      },
    },
    /* 「版本」原来单独一列只放一个「V1」。并进单号下面一行小字，
       九列变八列，表格才收得进内容区。 */
    {
      title: '报价单号',
      dataIndex: 'quote_no',
      /* 175 放不下「箭头 + 单号 + 版本」一整行，版本被挤成「第 …」；190 刚好 */
      width: compact ? 160 : 195,
      /* 原来单号与「第 N 版」是上下两行的块级 div —— 左边那个展开箭头是 inline 的，
         块级内容一定另起一行，于是箭头独占一行、单号再一行、版本第三行。
         改成同一行的两个 inline 片段：箭头 + 单号 + 小字版本，整行只占一行高。 */
      render: (v: string | null, record: ApprovalRow) =>
        compact ? (
          /* 紧凑档把版本号掉到第二行：单号 17 位字符 + 「V1」并排要塞进 156px，
             单号会被截成省略号。拆两行后单号完整可读，行高由客户列的第二行兜住。 */
          <div style={{ display: 'grid', gap: 2, justifyItems: 'center' }}>
            <span>{v ?? '-'}</span>
            {record.version_no ? (
              <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>V{record.version_no}</span>
            ) : null}
          </div>
        ) : (
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
    {
      title: '客户',
      dataIndex: 'customer_name',
      width: compact ? 150 : 235,
      // 紧凑档把申请人并进这一格第二行，省掉一列，避免 1024 宽溢出
      render: (v: string | null, record: ApprovalRow) => (
        <div style={{ display: 'grid', gap: 2, justifyItems: 'center', minWidth: 0 }}>
          <span
            title={v ?? '-'}
            style={{ maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
          >
            {v ?? '-'}
          </span>
          {compact && (
            <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
              申请人：{record.applicant_name ?? '-'}
            </span>
          )}
        </div>
      ),
    },
    ...(compact
      ? []
      : [{ title: '申请人', dataIndex: 'applicant_name', width: 100, render: (v: string | null) => v ?? '-' }]),
    {
      title: '报价总额',
      dataIndex: 'total_amount',
      width: compact ? 112 : 130,
      // 金额走全站口径右对齐（.cell-right）：全站表格默认居中，
      // 一列 ¥84,030.00 / ¥1.00 长短不一时居中读不出量级。
      className: 'cell-right',
      // 报价总额是业务单据上的钱，按全站口径必须两位小数（¥84,030.00），
      // 不能像统计卡那样取整。
      render: (v: number | null) => (v === null || v === undefined ? '-' : formatMoney(toTwoDecimals(v), 'CNY')),
    },
    {
      title: (
        <span className="crm-help-label">
          状态
          <HelpTip forLabel="状态" text="待审批 = 正在等人处理；待财务会签 = 本级已通过，停在追加的财务会签节点；规则免审 = 满足规则条件，系统自动放行。" />
        </span>
      ),
      dataIndex: 'status_label',
      width: compact ? 96 : 130,
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
            {record.status === 'pending' && record.summary?.current_assignee_name && (
              <span className="approval-assignee">由 {record.summary.current_assignee_name} 处理</span>
            )}
            {record.summary?.auto_passed && <Tag color="green" size="small">规则免审</Tag>}
          </div>
        )
      },
    },
    {
      title: '操作',
      width: compact ? 150 : 240,
      /* 原来是四个 <a>：看报价蓝、通过绿、拒绝红、转交深灰，字重一样、间距一样，
         读起来是"一排同重量的小字"。审批页真正的决策只有两个（通过 / 拒绝），
         看报价和转交只是配角。
         改成按钮层级：通过 = 主按钮（白底主色描边，见全局规则），拒绝 = 红色文字按钮，
         看报价 / 转交 = 普通文字按钮。同一屏里一眼看出"该点哪个"。 */
      render: (_: unknown, record: ApprovalRow) => (
        <div
          style={{
            display: 'flex',
            gap: 4,
            flexWrap: 'wrap',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          {record.version_id && (
            <Link to={`/quotes/${record.quote_id}?version=${record.version_id}`}>
              <Button theme="borderless" size="small">看报价</Button>
            </Link>
          )}
          {record.can_approve && (
            <>
              {/* 审批通过是不可逆的状态推进（报价随即可以对客发送），点一下生效太轻，
                  必须二次确认。「拒绝」本来就走原因弹窗，那条不用加。 */}
              <Popconfirm
                title="通过这份报价的审批？"
                content="确认已核对金额和优惠。需要会签的报价会先交给会签人，全部审批通过后才能发送。"
                onConfirm={() => approveMutation.mutate(record.id)}
              >
                {/* 2026-11-20：行内「通过」原来是白底描边，和同排的 看报价/拒绝/转交
                    三个蓝字不是一套；列表行内的动作一律文字按钮。 */}
                <Button type="tertiary" theme="borderless" size="small">通过</Button>
              </Popconfirm>
              <Button type="danger" theme="borderless" size="small" onClick={() => {
                setRejectComment('')
                setRejectTarget(record)
              }}>
                拒绝
              </Button>
              {(can('quote:approve') || can('quote:finance_approve')) && (
                <Button
                  theme="borderless"
                  size="small"
                  onClick={() => {
                    setTransferTarget(record)
                    setTransferUserId(null)
                    setTransferComment('')
                  }}
                >
                  转交
                </Button>
              )}
            </>
          )}
        </div>
      ),
    },
  ]

  return (
    <div className="page-container">
      <PageHeader
        title="审批中心"
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
            {Boolean(query.data?.items?.length) && <p className="workflow-guide">{APPROVAL_GUIDE[activeKey].next}</p>}
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
              /* 展开按钮已挪进最前面那一列（见 columns[0]），Semi 自带的
                 expandIcon 关掉，避免同一行出现两个入口。 */
              expandIcon={() => <span />}
              expandedRowKeys={expandedKeys}
              expandedRowRender={(record?: ApprovalRow) => {
                if (!record) return null
                const reason = record.summary?.reason
                /* 「整单（优惠后）」那条的说明跟申请说明是同一句话（同一个 reason 字段
                   被复写了一份），展开后就是「申请说明：…-257.14%」下面再原样重复一遍，
                   读起来像系统出了两次一样的错（2026-11-20 截图：三行里两行一模一样）。
                   只留那些真正不一样的（明细级的超权限行），整单那句由申请说明表达。 */
                const offending = (record.summary?.offending ?? []).filter(item => item.reason !== reason)
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
              pagination={(query.data?.total ?? 0) <= pageSize ? false : {
                currentPage: page,
                pageSize,
                total: query.data?.total ?? 0,
                onPageChange: setPage,
                showSizeChanger: true,
                pageSizeOpts: [10, 20, 50, 100],
                onPageSizeChange: (size: number) => { setPageSize(size); setPage(1) },
              }}
              empty={emptyText(query, APPROVAL_GUIDE[activeKey].empty, APPROVAL_GUIDE[activeKey].next)}
              scroll={{ x: '100%' }}
            />
          </div>
        )}
        {/* 分页条收起后总数没人说，补一句。
            「规则配置」页签下没有审批单列表，query 是关掉的、total 恒为 0，
            这里照样渲染就变成规则页右下角挂着「共 0 条」，而表里明明有四条
            （2026-11-20 截图）。总数交给规则表自己报。 */}
        {activeKey !== 'rules' && ((query.data?.total ?? 0) <= pageSize) && (
          <div style={{ marginTop: 10, textAlign: 'right', fontSize: 12, color: 'var(--crm-text-3)' }}>
            共 {query.data?.total ?? 0} 条
          </div>
        )}
      </SectionCard>

      <Modal
        width={520}
        style={{ maxWidth: 'calc(100vw - 32px)' }}
        title={`转交审批：${transferTarget?.quote_no ?? ''}`}
        visible={Boolean(transferTarget)}
        onCancel={() => setTransferTarget(null)}
        onOk={() => {
          if (canConfirmTransfer) transferMutation.mutate()
        }}
        confirmLoading={transferMutation.isPending}
        okButtonProps={{ disabled: !canConfirmTransfer }}
        okText="确认转交"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <p className="workflow-guide">转交后，由接收人继续处理当前审批。请从下方选择接收人。</p>
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
          {usersQuery.isLoading && <p className="workflow-guide">正在查找可接收的同事…</p>}
          {usersQuery.isError && <div role="alert">
            <p className="workflow-guide">未能读取接收人。{usersQuery.error.message}</p>
            <Button size="small" theme="outline" onClick={() => void usersQuery.refetch()}>重新查询接收人</Button>
          </div>}
          {usersQuery.isSuccess && !usersQuery.data.length && <p className="workflow-guide">当前没有可接收这笔审批的同事。</p>}
          <Input
            value={transferComment}
            onChange={setTransferComment}
            placeholder="转交说明（可选），例如：我在出差，麻烦处理"
          />
        </div>
      </Modal>

      <Modal
        width={520}
        style={{ maxWidth: 'calc(100vw - 32px)' }}
        title={`拒绝报价审批${rejectTarget?.quote_no ? ` · ${rejectTarget.quote_no}` : ''}`}
        visible={Boolean(rejectTarget)}
        onCancel={() => setRejectTarget(null)}
        onOk={() => rejectMutation.mutate()}
        confirmLoading={rejectMutation.isPending}
        okText="确认拒绝"
      >
        <p className="workflow-guide">拒绝后，这个版本无法正式发送。写清需调整的地方，申请人会收到审批意见。</p>
        <Input
          value={rejectComment}
          onChange={setRejectComment}
          placeholder="拒绝原因，例如：低于成本，不可让价"
        />
      </Modal>
    </div>
  )
}
