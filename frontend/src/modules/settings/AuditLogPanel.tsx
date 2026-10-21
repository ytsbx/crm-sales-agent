import { formatBeijingTime } from '../../shared/beijing-time'
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, DatePicker, Input, Select, SideSheet, Table, Tag, Typography } from '@douyinfe/semi-ui'

import { listAuditLogs, type AuditLogRow } from '../../shared/api/analytics'
import { listUsers } from '../../shared/api/system'
import {
  auditActionLabel,
  auditChangeSummary,
  auditHiddenFieldCount,
  auditTypeLabel,
  ENUM_LABEL,
} from '../../shared/audit-labels'

/**
 * 审计日志：搜索 / 筛选 / 分页 + 变更详情。
 *
 * ## 为什么要有这个面板（2026-10-15）
 *
 * 主人原话：「审计日志里面的信息会一直留着吗，还有现在太乱了，也没有搜索功能」。
 * 查下来问题不在后端 —— 后端**一直**支持 `keyword` / `business_type` / `action` /
 * `operator_id` / `start` / `end` 六个筛选和分页，是页面：
 *
 *   · 查询写死 `{ page: 1, page_size: 50 }` → **只能看到最近 50 条**，更早的翻不到；
 *   · `pagination={false}` → 分页关掉，没有"下一页"；
 *   · 界面上**一个筛选框都没有** → 一屏流水，不能搜、不能筛。
 *
 * 所以这一页是"把已有的能力接出来"，不是新做检索。
 *
 * ## 两处容易出错的地方
 *
 * 1. **搜索要认中文**。界面上显示的是中文（「定制需求」「修改状态」），库里存的是
 *    英文码（`custom_inquiry`、`update_status`）。后端为此加了中文反查
 *    （`app/core/audit_search.py`）；前端这边**只把用户输入原样传过去**，
 *    不要在本地先翻译 —— 不然"按编号搜""按人名搜"这些就都被拦掉了。
 *
 * 2. **操作人下拉的数据源**。审计只能管理员看，而 `/users` 也是管理员才有的权限，
 *    所以这里可以放心用；但**不要**因为拿不到用户列表就让筛选失效 ——
 *    下拉是空的，搜索框仍然要能用。
 */
const DEFAULT_PAGE_SIZE = 15
const PAGE_SIZE_OPTIONS = [15, 30, 50, 100]

export interface AuditLogPanelProps {
  /** 当前是否在审计页签（不在时不发请求，避免无谓的拉取） */
  active: boolean
}

export default function AuditLogPanel({ active }: AuditLogPanelProps) {
  const [keywordInput, setKeywordInput] = useState('')
  // 关键字与输入框分开：输入框每敲一下都会变，直接进 queryKey 会每敲一下就发一次请求
  const [keyword, setKeyword] = useState('')
  const [businessType, setBusinessType] = useState<string | undefined>()
  const [action, setAction] = useState<string | undefined>()
  const [operatorId, setOperatorId] = useState<number | undefined>()
  const [range, setRange] = useState<[Date, Date] | undefined>()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [detail, setDetail] = useState<AuditLogRow | null>(null)

  // 输入防抖：审计是海量流水，逐字发请求既慢又吵
  useEffect(() => {
    const timer = setTimeout(() => {
      setKeyword(keywordInput.trim())
      setPage(1)
    }, 300)
    return () => clearTimeout(timer)
  }, [keywordInput])

  const usersQuery = useQuery({
    queryKey: ['audit-operator-options'],
    queryFn: () => listUsers({ page: 1, page_size: 200 }),
    enabled: active,
    staleTime: 5 * 60 * 1000,
  })

  const query = useQuery({
    queryKey: ['audit-logs', page, pageSize, keyword, businessType, action, operatorId, range?.[0]?.toISOString(), range?.[1]?.toISOString()],
    queryFn: () =>
      listAuditLogs({
        page,
        page_size: pageSize,
        keyword: keyword || undefined,
        business_type: businessType,
        action,
        operator_id: operatorId,
        // 时间范围：后端按 created_at 过滤。结束那天要**含当天**
        // （用户选"到今天"是想看到今天发生的，而不是到昨天 24:00 为止）。
        start: range?.[0] ? new Date(range[0].setHours(0, 0, 0, 0)).toISOString() : undefined,
        end: range?.[1] ? new Date(range[1].setHours(23, 59, 59, 999)).toISOString() : undefined,
      }),
    enabled: active,
    // 翻页时保留上一页的内容，避免表格闪成空白
    placeholderData: (prev) => prev,
  })

  const rows = query.data?.items ?? []
  const total = query.data?.total ?? 0
  const operatorOptions = (usersQuery.data?.items ?? []).map((u: { id: number; name?: string | null; username?: string }) => ({
    value: u.id,
    label: u.name || u.username,
  }))

  const hasFilter = Boolean(
    keyword || businessType || action || operatorId || range,
  )
  const resetAll = () => {
    setKeywordInput('')
    setKeyword('')
    setBusinessType(undefined)
    setAction(undefined)
    setOperatorId(undefined)
    setRange(undefined)
    setPage(1)
    setPageSize(DEFAULT_PAGE_SIZE)
  }

  return (
    <>
      {/* 筛选条：搜索 + 对象 + 动作 + 操作人 + 时间范围。
          搜索框的提示语要写清"中文也能搜"，否则用户不会去试。 */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12, alignItems: 'center' }}>
        <Input
          style={{ width: 240 }}
          prefix="搜索"
          showClear
          placeholder="对象、动作、操作人、编号（中文也行）"
          value={keywordInput}
          onChange={setKeywordInput}
        />
        <Select
          style={{ width: 160 }}
          placeholder="全部对象类型"
          showClear
          value={businessType}
          onChange={(v) => {
            setBusinessType(v as string | undefined)
            setPage(1)
          }}
          optionList={[
            { value: 'sample', label: '打样单' },
            { value: 'custom_inquiry', label: '定制需求' },
            { value: 'quote', label: '报价单' },
            { value: 'quote_version', label: '报价版本' },
            { value: 'customer', label: '客户' },
            { value: 'lead', label: '线索' },
            { value: 'opportunity', label: '商机' },
            { value: 'order', label: '订单' },
            { value: 'payment', label: '回款' },
            { value: 'user', label: '用户' },
            { value: 'role', label: '角色' },
            { value: 'auth', label: '登录与账号' },
            { value: 'integration', label: '外部集成' },
            { value: 'agent_session', label: 'AI 助手会话' },
          ]}
        />
        <Select
          style={{ width: 150 }}
          placeholder="全部动作"
          showClear
          filter
          value={action}
          onChange={(v) => {
            setAction(v as string | undefined)
            setPage(1)
          }}
          optionList={[
            { value: 'create', label: '新建' },
            { value: 'update', label: '修改' },
            { value: 'update_status', label: '修改状态' },
            { value: 'approve', label: '审批通过' },
            { value: 'reject', label: '驳回' },
            { value: 'submit_approval', label: '提交审批' },
            { value: 'assign', label: '分配负责人' },
            { value: 'export', label: '导出' },
            { value: 'import', label: '导入' },
            { value: 'login', label: '登录' },
          ]}
        />
        <Select
          style={{ width: 150 }}
          placeholder="全部操作人"
          showClear
          filter
          value={operatorId}
          onChange={(v) => {
            setOperatorId(v as number | undefined)
            setPage(1)
          }}
          optionList={operatorOptions}
        />
        <DatePicker
          type="dateRange"
          style={{ width: 250 }}
          placeholder={['开始日期', '结束日期']}
          value={range}
          onChange={(v) => {
            setRange(v as [Date, Date] | undefined)
            setPage(1)
          }}
        />
        {hasFilter && (
          <Button theme="borderless" onClick={resetAll}>
            清除筛选
          </Button>
        )}
        <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
          共 {total} 条
        </span>
      </div>

      <Table<AuditLogRow>
        loading={query.isFetching}
        dataSource={rows}
        rowKey="id"
        empty="没有符合条件的记录"
        pagination={{
          currentPage: page,
          pageSize,
          total,
          onPageChange: setPage,
          showSizeChanger: true,
          pageSizeOpts: PAGE_SIZE_OPTIONS,
          onPageSizeChange: (size: number) => {
            setPageSize(size)
            setPage(1)
          },
          showTotal: true,
        }}
        columns={[
          {
            title: '时间',
            dataIndex: 'created_at',
            width: 170,
            render: (v: string) => formatBeijingTime(v),
          },
          {
            title: '操作人',
            dataIndex: 'operator_name',
            width: 100,
            render: (v: string | null) => v ?? '系统',
          },
          {
            title: '对象',
            width: 180,
            render: (_: unknown, record: AuditLogRow) =>
              record.business_type
                ? `${auditTypeLabel(record.business_type)}${
                    record.business_id != null ? ` 编号 ${record.business_id}` : ''
                  }`
                : '-',
          },
          {
            title: '动作',
            dataIndex: 'action',
            width: 130,
            render: (v: string) => auditActionLabel(v),
          },
          {
            // 来源：只显示中文，不再印 `WEB`/`AGENT` 这种码
            title: '来源',
            dataIndex: 'source',
            width: 100,
            render: (v: string | null) => (v ? (ENUM_LABEL[v] ?? v) : '-'),
          },
          {
            // 变更内容：摘要给人看，**原文不能丢** —— 这一页是合规取证用的。
            // 翻不出来的字段不硬凑英文名，而是给一个"看原始数据"的入口。
            title: '变更内容',
            render: (_: unknown, record: AuditLogRow) => {
              const summary = auditChangeSummary(record)
              const hidden = auditHiddenFieldCount(record)
              const raw = record.after_data ?? record.before_data
              return (
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ flex: 1 }}>{summary || '-'}</span>
                  {hidden > 0 && <Tag size="small" color="grey">{`还有 ${hidden} 项`}</Tag>}
                  {raw && (
                    <Button size="small" theme="borderless" onClick={() => setDetail(record)}>
                      明细
                    </Button>
                  )}
                </div>
              )
            },
          },
        ]}
      />

      {/* 原始数据：只在点「明细」时展开，平时不占屏幕 —— 审计页一屏 50 条，
          每条都摊开 JSON 就没法看了 */}
      <SideSheet
        title="变更明细"
        visible={detail !== null}
        onCancel={() => setDetail(null)}
        width={560}
      >
        {detail && (
          <div style={{ display: 'grid', gap: 12 }}>
            <Typography.Text type="tertiary">
              {`${formatBeijingTime(detail.created_at)} · ${
                detail.operator_name ?? '系统'
              } · ${auditTypeLabel(detail.business_type)}${
                detail.business_id != null ? ` 编号 ${detail.business_id}` : ''
              } · ${auditActionLabel(detail.action)}`}
            </Typography.Text>
            {detail.ip && (
              <Typography.Text type="tertiary">{`来源 IP：${detail.ip}`}</Typography.Text>
            )}
            {detail.before_data && (
              <div>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>改之前</div>
                <pre style={preStyle}>{JSON.stringify(detail.before_data, null, 2)}</pre>
              </div>
            )}
            {detail.after_data && (
              <div>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>改之后</div>
                <pre style={preStyle}>{JSON.stringify(detail.after_data, null, 2)}</pre>
              </div>
            )}
            {!detail.before_data && !detail.after_data && (
              <Typography.Text type="tertiary">这条记录没有留存字段快照。</Typography.Text>
            )}
          </div>
        )}
      </SideSheet>
    </>
  )
}

const preStyle: React.CSSProperties = {
  margin: 0,
  padding: 12,
  background: 'var(--crm-fill-0)',
  borderRadius: 6,
  fontSize: 12,
  lineHeight: 1.6,
  maxHeight: 320,
  overflow: 'auto',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-all',
}
