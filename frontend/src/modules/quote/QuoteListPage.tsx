import { useEffect, useRef, useState } from 'react'
import PageHeader from '../../shared/components/PageHeader'
import { formatMoney } from '../../shared/components/money'
import SectionCard from '../../shared/components/SectionCard'
import { Link, useNavigate } from 'react-router-dom'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { emptyText } from '../../shared/hooks/emptyText'
import { Button, Input, Modal, Select, Table, Toast } from '@douyinfe/semi-ui'

import { listOpportunities } from '../../shared/api/opportunity'
import { createQuote, listQuotes, type Quote } from '../../shared/api/quote'
import { newRequestKey } from '../../shared/api/requestKey'
import { usePermissions } from '../../shared/hooks/permissions'
import type { TagTone } from '../../shared/types'
import { optionsWithCodeOnDuplicate } from '../../shared/components/optionMatch'
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
  const canViewOpportunities = can('opportunity:view')

  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState<string | undefined>()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [createVisible, setCreateVisible] = useState(false)
  const [selectedOpportunity, setSelectedOpportunity] = useState<{ label: string; value: number } | null>(null)
  const [opportunitySearch, setOpportunitySearch] = useState('')
  const [opportunityKeyword, setOpportunityKeyword] = useState('')
  // 一次"新建报价"意图一把键（第八批 8.15）：在 mutationFn 里现生成的话，
  // 弱网重试会换一把新键，服务端仍会建出第二条报价 —— 幂等就白做了。
  const createRequestKeyRef = useRef('')

  /* 1024 宽时内容区约 704px，七列固定宽度合计 825px，「有效期 / 负责人」
     两列被裁到卡片外（2026-11-18）。窄屏切紧凑档：列宽收紧，负责人并进
     客户/商机的第二行小字，七列变六列 —— 不出现横向滚动条。 */
  const [compact, setCompact] = useState(() => window.innerWidth < 1200)
  useEffect(() => {
    const onResize = () => setCompact(window.innerWidth < 1200)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const query = useQuery({
    queryKey: ['quotes', { keyword, status, page, pageSize }],
    queryFn: () => listQuotes({ keyword, status, page, page_size: pageSize }),
  })
  useEffect(() => {
    const timer = setTimeout(() => setOpportunityKeyword(opportunitySearch.trim()), 250)
    return () => clearTimeout(timer)
  }, [opportunitySearch])
  const opportunitiesQuery = useInfiniteQuery({
    queryKey: ['opportunities-for-quote', opportunityKeyword],
    initialPageParam: 1,
    queryFn: ({ pageParam }) => listOpportunities({
      status: 'open', keyword: opportunityKeyword, search_customer_name: true, page: pageParam, page_size: 20,
    }),
    getNextPageParam: (last, pages) =>
      last.items.length > 0 && pages.length * 20 < last.total ? pages.length + 1 : undefined,
    enabled: createVisible && canViewOpportunities,
  })
  const opportunityOptions = optionsWithCodeOnDuplicate(
    opportunitiesQuery.data?.pages.flatMap((result) => result.items) ?? [],
    (item) => `${item.title}（${item.customer_name ?? ''}）`,
    (item) => item.id,
  )
  if (selectedOpportunity && !opportunityOptions.some(({ value }) => value === selectedOpportunity.value)) {
    opportunityOptions.push(selectedOpportunity)
  }

  const createMutation = useMutation({
    mutationFn: () =>
      createQuote({
        opportunity_id: selectedOpportunity!.value,
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
      setSelectedOpportunity(null)
      void queryClient.invalidateQueries({ queryKey: ['quotes'] })
      navigate(`/quotes/${data.quote_id}`)
    },
    // 失败**不换键**：用户改完重提时服务端会释放占位，同一把键仍可重试
    onError: (error: Error) => Toast.error(error.message),
  })

  /* 审批状态点：宽屏单独一列，窄屏并进「状态」列第二行 */
  const approvalDot = (value: string | null | undefined) =>
    value === 'approved' ? (
      <StatusDot label="已通过" tone="green" />
    ) : value === 'pending' ? (
      <StatusDot label="审批中" tone="orange" />
    ) : value === 'rejected' ? (
      <StatusDot label="被拒" tone="red" />
    ) : (
      <StatusDot label="未提交" tone="grey" />
    )

  const columns = [

    {
      // 版本号并到单号下面一行小字：V1/V2 单独占一整列（100px 放两个字）太奢侈，
      // 而看单子又必须知道是第几版 —— 和单号放一起最省地方
      title: '报价单号',
      dataIndex: 'quote_no',
      width: compact ? 124 : 145,
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
      width: compact ? 168 : 175,
      render: (v: string | null, record: Quote) => (
        <div style={{ minWidth: 0 }}>
          <div className="cell-strong" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={v ?? '-'}>
            {v ?? '-'}
          </div>
          <div
            title={`${record.opportunity_title ?? '未关联商机'}${compact ? ` · 负责人 ${record.owner_name ?? '-'}` : ''}`}
            style={{
              marginTop: 2,
              fontSize: 12,
              color: 'var(--crm-text-3)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {record.opportunity_title ?? '未关联商机'}
            {compact ? ` · 负责人 ${record.owner_name ?? '-'}` : ''}
          </div>
        </div>
      ),
    },
    {
      title: '报价总额',
      dataIndex: 'current_version_amount',
      /* 窄屏 640 下这一列被压到 90 出头，"¥135,000.00" 会被省略号吞掉
         尾巴（金额被截断比文字截断更糟：读者无法判断量级）。加宽并禁掉省略。 */
      width: compact ? 124 : 115,
      ellipsis: false,
      /* 金额右对齐：位数不同的报价左对齐时右边界参差，
         ¥100.00 和 ¥34,343.00 摆一起读不出量级（与回款中心一致）。 */
      align: 'right' as const,
      className: 'cell-right',
      render: (v: number | null, record: Quote) => (
        <span style={{ whiteSpace: 'nowrap', fontSize: compact ? 12 : undefined }}>
          {formatMoney(record.current_version_amount_exact ?? v, record.currency)}
        </span>
      ),
    },
    {
      title: compact ? '状态 / 审批' : '报价状态',
      dataIndex: 'status_label',
      width: compact ? 92 : 100,
      render: (value: string, record: Quote) =>
        compact ? (
          /* 窄屏把「审批状态」并进这一列的第二行，省掉一列宽度 */
          <div style={{ display: 'grid', gap: 2, justifyItems: 'center' }}>
            <StatusDot label={value} tone={(STATUS_TONE[record.status] ?? 'grey') as TagTone} />
            {approvalDot(record.approval_status)}
          </div>
        ) : (
          <StatusDot label={value} tone={(STATUS_TONE[record.status] ?? 'grey') as TagTone} />
        ),
    },
    ...(compact
      ? []
      : [{
          title: '审批',
          dataIndex: 'approval_status',
          width: 95,
          render: (value: string | null) => approvalDot(value),
        }]),
    /* 窄屏（640）不放有效期：五列挤在 590px 里，日期只露出「20」两个字符，
       还不如不给 —— 报价单号、客户、金额、状态四个才是列表里要扫的东西。 */
    ...(compact
      ? []
      : [{
          title: '有效期',
          dataIndex: 'valid_until',
          width: 105,
          render: (v: string | null) => v ?? '-',
        }]),
    ...(compact
      ? []
      : [{ title: '负责人', dataIndex: 'owner_name', width: 90, render: (v: string | null) => v ?? '-' }]),
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
            type="tertiary"
            theme="borderless"
            onClick={() => {
            setKeyword(keywordInput.trim())
            setPage(1)
            }}
          >
            查询
          </Button>
          <div style={{ flex: 1 }} />
          {can('quote:manage') && (
            <Button type="primary"
              theme="outline"
              onClick={() => {
                // 每次打开"新建报价"都是一次新意图 → 换一把新的幂等键
                createRequestKeyRef.current = newRequestKey()
                setOpportunitySearch('')
                setOpportunityKeyword('')
                setCreateVisible(true)
              }}
            >
              新建报价
            </Button>
          )}
        </div>

        <Table<Quote>
          /* 固定列宽：不设的话表格按内容撑宽，商机那行小字会把客户列顶到 220px，
             「审批 / 有效期」被挤出卡片（1024 宽）。 */
          tableLayout="fixed"
          className="quotes-table"
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
          if (!selectedOpportunity) {
            Toast.warning('请选择商机')
            return
          }
          createMutation.mutate()
        }}
        confirmLoading={createMutation.isPending}
        /* 没选商机时按钮是灰的，并且把「缺什么」写在按钮上方常驻一行 ——
           原来只在点了以后弹 Toast，主人定的规矩是禁用控件要当场说明原因。 */
        okButtonProps={{ disabled: !canViewOpportunities || !selectedOpportunity }}
        okText="生成报价单"
      >
        <div style={{ marginBottom: 8 }}>选择商机（明细取自该商机的需求商品）</div>
        {/* 原来写「去「查价」页」—— 左侧菜单里没有叫「查价」的页，
            那个入口其实叫「价格中心」（2026-11-20 截图）。按菜单名写。 */}
        <div style={{ marginBottom: 12, fontSize: 12, color: 'var(--crm-text-3)' }}>
          报价必须关联商机。还没有合适商机？去「价格中心」搜 SKU 选品，那里能一键新建快捷商机。
        </div>
        {canViewOpportunities ? <Select
          placeholder="按商机或客户名称搜索"
          value={selectedOpportunity?.value}
          onChange={(value) => setSelectedOpportunity(opportunityOptions.find((option) => option.value === value) ?? null)}
          optionList={opportunityOptions}
          filter
          remote
          onSearch={setOpportunitySearch}
          loading={opportunitiesQuery.isFetching}
          outerBottomSlot={opportunitiesQuery.isError ? (
            <Button theme="borderless" onClick={() => {
              if (opportunitiesQuery.isFetchNextPageError) void opportunitiesQuery.fetchNextPage()
              else void opportunitiesQuery.refetch()
            }}>加载失败，点击重试</Button>
          ) : opportunitiesQuery.hasNextPage ? (
            <Button theme="borderless" loading={opportunitiesQuery.isFetchingNextPage} onClick={() => {
              if (!opportunitiesQuery.isFetching && opportunitySearch.trim() === opportunityKeyword) {
                void opportunitiesQuery.fetchNextPage()
              }
            }}>加载更多商机</Button>
          ) : undefined}
          style={{ width: '100%' }}
        /> : <p className="workflow-guide">当前账号没有查看商机的权限。请联系管理员开通，再新建报价。</p>}
        {canViewOpportunities && !selectedOpportunity && (
          <p className="workflow-guide" style={{ marginTop: 8 }}>
            先在上面选一个商机，「生成报价单」才可用。
          </p>
        )}
      </Modal>
    </div>
  )
}
