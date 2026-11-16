import { formatBeijingTime } from '../../shared/beijing-time'
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, DatePicker, Input, Radio, RadioGroup, Select, SideSheet, Table, Tag, Typography } from '@douyinfe/semi-ui'

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
/* 默认每页 30（原来 15）：登录/登出占了全部记录的八成，一页 15 条时
   一眼望去全是登录，想翻到一条业务变更要翻十几页（2026-11-16 主人「看的慢」）。 */
const DEFAULT_PAGE_SIZE = 30
const PAGE_SIZE_OPTIONS = [15, 30, 50, 100]

/**
 * 动作按「这件事有没有结果」分三档上色（2026-11-16 视觉优化）。
 *
 * 这一页打开时一屏几乎全是「登录」（库里 530 条有 290 条是登录，占一半以上），
 * 六列同样的灰字，扫一眼分不出哪条值得看。所以动作列不再一律黑字：
 *
 *   · 绿 = 有结果（审批通过、客户接受、中标、完成…），一眼看到"事情落地了"；
 *   · 红 = 被否掉（驳回、失单、作废、撤回审批…），出事要第一眼看到；
 *   · 青 = 新产生的（新建、生成、导入、上传…），"无中生有"这一类；
 *   · 蓝 = 其余常规动作（修改、提交、发送、替换明细…），有信息量但不用喊；
 *   · 登录 / 退出登录**不上色、不加标签**，压成浅灰小字 ——
 *     它只是流水，把它染成和「审批通过」一样重，等于把噪音当信号。
 *
 * 显式表之外的动作用词根兜底：动作表是后端在扩的（现在 164 个），
 * 不能每加一个就回来补一行，否则颜色很快就跟不上。
 */
const ACTION_TONE: Record<string, 'green' | 'red'> = {
  approve: 'green',
  accept: 'green',
  confirm: 'green',
  confirm_win: 'green',
  win: 'green',
  complete: 'green',
  resolve_customer_duplicate: 'green',
  confirm_schedule_change: 'green',
  reject: 'red',
  lose: 'red',
  void: 'red',
  withdraw_approval: 'red',
}
/** 「撤回审批」里带着 approve，所以显式表必须先生效，词根兜底排在它后面。 */
const GREEN_ACTION_HINTS = ['approve', 'confirm', 'win', 'accept', 'complete', 'resolve', 'settle', 'paid']
const RED_ACTION_HINTS = ['reject', 'lose', 'void', 'cancel', 'decline', 'discard', 'delete', 'remove', 'withdraw', 'fail']
/**
 * 「新产生了一条东西」这一类，单独给青色。
 * 不往 `add` 这种两三个字母的短词上挂：`includes` 是子串匹配，
 * `add` 会连带命中 `address_...` 这种根本不是新建的动作码。
 */
const CREATION_ACTION_HINTS = ['create', 'new', 'generate', 'import', 'upload', 'copy']
/** 纯流水：不染色、不加标签，压成浅灰小字。 */
const QUIET_ACTIONS = new Set(['login', 'logout'])

/**
 * 对象列要显示"这是哪一张单 / 哪一个人"，而不是内部编号。
 *
 * 后端 /audit-logs 只给 business_type + business_id（不像核价历史那样带
 * target_label），所以这里从这条记录自己的快照里找可读标识：单号、名字、编码。
 * 找不到就只显示类型 —— 宁可少一段，也不要把 `编号 198` 这种"内部数据库
 * 自增号"摆在用户面前（2026-11-16 主人：不用显示编号）。
 */
const OBJECT_LABEL_KEYS = [
  'quote_no', 'order_no', 'sample_no', 'contract_no', 'inquiry_no', 'doc_no',
  'customer_name', 'name', 'title', 'code', 'username',
]

function objectLabel(record: AuditLogRow): string | null {
  const snapshots = [record.after_data, record.before_data]
  for (const snap of snapshots) {
    if (!snap) continue
    for (const key of OBJECT_LABEL_KEYS) {
      const value = snap[key]
      if (typeof value === 'string' && value.trim()) return value.trim()
    }
  }
  return null
}

function actionTone(action: string): 'blue' | 'green' | 'red' | 'cyan' {
  const explicit = ACTION_TONE[action]
  if (explicit) return explicit
  if (RED_ACTION_HINTS.some((hint) => action.includes(hint))) return 'red'
  if (GREEN_ACTION_HINTS.some((hint) => action.includes(hint))) return 'green'
  if (CREATION_ACTION_HINTS.some((hint) => action.includes(hint))) return 'cyan'
  return 'blue'
}

/**
 * 三个动作码在共用字典（`backend/app/core/audit_action_labels.json`）里还没登记，
 * 但库里已经写下了记录 —— 直接渲染就是英文原码上屏（全站禁止）。
 * 在前端补上中文，**不去动后端那份字典真源**：真源由 `ops/check_audit_labels.py`
 * 和另一个会话在管，这里只做显示兜底。
 */
const ACTION_LABEL_PATCH: Record<string, string> = {
  update_status: '修改状态',
  sku_master_recheck: 'SKU 来源复核',
  sku_master_confirm_local: 'SKU 本地确认',
}

/**
 * 动作下拉的选项：**按库里真实频次排**（2026-11-16）。
 *
 * 原来只写了 10 个，而库里实际出现过 36 种 ——「提交审批」「客户接受」
 * 「推进阶段」「标记成交」这些最该拿来筛的动作反而选不到，筛选等于没用。
 * 常用的排前面，配合 `filter` 直接敲字找，不用滚到底。
 */
const ACTION_OPTION_CODES = [
  'login', 'create', 'update', 'submit_approval', 'set_items', 'accept', 'change_stage',
  'confirm', 'send', 'win', 'approve', 'logout', 'confirm_win', 'price_refresh',
  'quote_link_master', 'redispatch_notification', 'generate', 'change_status',
  'open_customer_duplicate_cases', 'resolve_customer_duplicate', 'sku_master_recheck',
  'update_status', 'ship_batch', 'create_shipment_batch', 'create_version', 'download',
  'lose', 'sku_master_confirm_local', 'wecom_sync_external_contacts', 'withdraw_approval',
  'complete', 'confirm_schedule_change', 'copy_version', 'create_schedule_change',
  'merge', 'repurchase', 'void', 'reject', 'assign', 'export', 'import',
]

const ACTION_OPTIONS = ACTION_OPTION_CODES.map((code) => ({
  value: code,
  label: ACTION_LABEL_PATCH[code] ?? auditActionLabel(code),
}))

/** 动作在界面上的中文名：先看兜底表，再走全站字典（认不出会退回原码，由 CI 兜住）。 */
function actionLabel(action: string | null | undefined): string {
  if (!action) return '-'
  return ACTION_LABEL_PATCH[action] ?? auditActionLabel(action)
}

/**
 * 「变更内容」这一列只有 280px，一行放得下约 20 个汉字，两行 40 个字。
 * 超出的部分直接截掉 —— 不是偷懒，是因为这页的表格是 table-layout:auto，
 * CSS 的省略号管不了"这一列最小要多宽"，长句会把整张表撑出容器。
 * 原文一点没丢：`title` 悬停看全文，右侧「明细」展开完整快照。
 */
const SUMMARY_MAX_CHARS = 40
function truncateSummary(summary: string): string {
  return summary.length > SUMMARY_MAX_CHARS ? `${summary.slice(0, SUMMARY_MAX_CHARS)}…` : summary
}

export interface AuditLogPanelProps {
  /** 当前是否在审计页签（不在时不发请求，避免无谓的拉取） */
  active: boolean
}

/**
 * 三档视图：全部 / 只看业务变更 / 只看登录。
 *
 * 库里登录（含登出、刷新令牌）占了八成，翻到一条业务变更要翻十几页。
 * 后端 2026-11-16 加了 `exclude_actions` 参数（支持"某几类不要"，
 * 而 `action` 只能"只要某一类"），这里就接成三个按钮。
 *
 * 注意"只看登录"只能传 `action=login`：后端没有 include 多选，
 * 所以登出、刷新令牌这两类在这一档里看不到（它们在"全部"里仍然在）。
 */
const LOGIN_ACTIONS = 'login,logout,refresh'

type AuditView = 'all' | 'biz' | 'login'

export default function AuditLogPanel({ active }: AuditLogPanelProps) {
  const [view, setView] = useState<AuditView>('all')
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

  // 换视图等于换了一套数据，页码必须归 1，否则会停在"第 8 页的第 8 页"那种空页
  const changeView = (next: AuditView) => {
    setView(next)
    setPage(1)
  }

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
    queryKey: ['audit-logs', view, page, pageSize, keyword, businessType, action, operatorId, range?.[0]?.toISOString(), range?.[1]?.toISOString()],
    queryFn: () =>
      listAuditLogs({
        page,
        page_size: pageSize,
        keyword: keyword || undefined,
        business_type: businessType,
        // 「只看业务变更」= 把登录/登出/刷新令牌三类排除掉，其余照常分页；
        // 「只看登录」= 后端 action 精确匹配 login（登出、刷新这一档看不到）。
        action: view === 'login' ? 'login' : action,
        exclude_actions: view === 'biz' ? LOGIN_ACTIONS : undefined,
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
    view !== 'all' || keyword || businessType || action || operatorId || range,
  )
  const resetAll = () => {
    setView('all')
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
          搜索框的提示语要写清"中文也能搜"，否则用户不会去试。

          宽度是算过的（2026-11-16）：内容区约 938px，
          240（搜索）+140（对象）+130（动作）+130（操作人）+220（日期）= 860，
          加四个 8px 间距 = 892，**一行放得下**。
          每个数不是拍出来的：`全部对象类型` 六个字要 140、日期框低于 220 会把
          「开始日期 ~ 结束日期」截成「开始…」，压窄了看着是"选中了什么都不知道"。原来 240+160+150+150+250 加一起
          950，必然换行，「共 N 条」被挤到第二行尾巴上 —— 好看不好看其次，
          第一行看着像断了一半才是真难看。 */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8, alignItems: 'center' }}>
        <Input
          style={{ width: 240 }}
          prefix="搜索"
          showClear
          // 提示语要短到能**在一行里放完**：200px 宽塞 18 个字会被截成
          // 「对象、动作、操作…」，恰好把"中文也行"这个关键提示切掉，
          // 用户就永远不会知道能按中文搜。
          placeholder="中文可搜对象、人名、编号"
          value={keywordInput}
          onChange={setKeywordInput}
        />
        <Select
          style={{ width: 140 }}
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
          style={{ width: 130 }}
          placeholder="全部动作"
          showClear
          filter
          value={action}
          onChange={(v) => {
            setAction(v as string | undefined)
            setPage(1)
          }}
          optionList={ACTION_OPTIONS}
        />
        <Select
          style={{ width: 130 }}
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
          style={{ width: 220 }}
          placeholder={['开始日期', '结束日期']}
          value={range}
          onChange={(v) => {
            setRange(v as [Date, Date] | undefined)
            setPage(1)
          }}
        />
      </div>

      {/* 结果规模 + 清除筛选：单独一行、左右分边，紧贴表格。
          这两样原来都挂在筛选条末尾 —— 计数被换行甩到第二行尾巴上，
          「清除筛选」一出现更是直接把筛选条挤成两行。
          现在筛选条只放五个控件，位置就稳了。 */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 8,
          minHeight: 24,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, minWidth: 0 }}>
          <RadioGroup
            type="button"
            buttonSize="small"
            value={view}
            onChange={(e) => changeView(e.target.value as AuditView)}
          >
            <Radio value="all">全部</Radio>
            <Radio value="biz">只看业务变更</Radio>
            <Radio value="login">只看登录</Radio>
          </RadioGroup>
          <span style={{ fontSize: 12, color: 'var(--crm-text-3)', whiteSpace: 'nowrap' }}>
            {hasFilter ? `筛选出 ${total} 条` : `共 ${total} 条`}
          </span>
        </div>
        {hasFilter && (
          <Button size="small" theme="borderless" onClick={resetAll}>
            清除筛选
          </Button>
        )}
      </div>

      <Table<AuditLogRow>
        loading={query.isFetching}
        dataSource={rows}
        rowKey="id"
        // 登录/登出整行压成三级灰。原来只把「动作」那一格变灰，时间、操作人、
        // 对象、来源还是正常色，整行照样抢眼；整行一起压下去，业务变更那几行
        // 才会自己浮出来（需要查登录时间时仍一眼能找到，没有隐藏）。
        rowClassName={(row: AuditLogRow) =>
          QUIET_ACTIONS.has(row.action) ? 'audit-quiet-row' : 'audit-biz-row'}
        // `scroll.x: '100%'` 不是"给个滚动宽度"，而是告诉 Semi「表格宽度就是容器宽度」。
        // 试过写死 `x: 900`，Semi 会把列宽和 scroll 宽度各算各的，最后一列被分到
        // 622px、整表反而撑到 1255（容器只有 923）。写 '100%' 之后各列按比例摊到
        // 容器里，不会再出现"表体 928 / 容器 923"这种 5px 的横向滚动。
        scroll={{ x: '100%' }}
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
          // 总数已经在表格右上角写着了，这里再印一次纯属重复
          showTotal: false,
        }}
        columns={[
          {
            // 时间**必须单行**。试过拆成「日期 / 时分秒」两行，宽度是省了 20px，
            // 但行高从 40 撑到 60，一屏从 15 条掉到 11 条 —— 为了省 20px 横向
            // 牺牲 4 条纵向信息，不划算。所以只把宽度从 170 收到 150（不换行）。
            title: '时间',
            dataIndex: 'created_at',
            width: 145,
            render: (v: string) => (
              <span style={{ whiteSpace: 'nowrap' }}>{formatBeijingTime(v)}</span>
            ),
          },
          {
            title: '操作人',
            dataIndex: 'operator_name',
            width: 95,
            // 没有操作人的记录（定时任务、集成回调）说「系统」，
            // 但用灰字 —— 它不是一个人名，别和真人姓名抢注意力。
            render: (v: string | null) =>
              v ? v : <span style={{ color: 'var(--crm-text-3)' }}>系统</span>,
          },
          {
            // 对象拆两段：类型正常字重 + 编号小灰字。
            // 原来「报价单 编号 3」整列一种字重，一个两位数是可有可无的定位信息，
            // 却和"这是哪类单据"占一样的分量。
            title: '对象',
            // 165 是量出来的：155 放不下「报价单 Q202611160010」（类型 42 + 空格 6 +
            // 单号 110 ≈ 158），190 让整表 942 超容器 923 多出 19px 横向滚动，
            // 170 仍剩 5px。Semi 摊平列宽时会吃几个像素，最后落在 165 ——
            // 总宽 919，容器 923，留 4px 余量（纵向滚动条一出现就要吃 15px）。
            width: 165,
            render: (_: unknown, record: AuditLogRow) => {
              if (!record.business_type) return '-'
              const label = objectLabel(record)
              return (
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, minWidth: 0 }}>
                  <span style={{ whiteSpace: 'nowrap' }}>{auditTypeLabel(record.business_type)}</span>
                  {label && (
                    <span
                      style={{
                        fontSize: 12,
                        color: 'var(--crm-text-2)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                      title={label}
                    >
                      {label}
                    </span>
                  )}
                </div>
              )
            },
          },
          {
            // 动作按"有没有结果"上色；登录/退出登录只留浅灰小字（见文件头 ACTION_TONE）
            title: '动作',
            dataIndex: 'action',
            width: 125,
            render: (v: string) => {
              if (!v) return '-'
              if (QUIET_ACTIONS.has(v)) {
                // 登录/登出不套彩色标签，但给一个 5px 灰圆点：一眼能认出"这是一条
                // 登录"，跟上面业务变更的彩色标签在形态上分开（2026-11-16）。
                return (
                  <span style={{ fontSize: 12, color: 'var(--crm-text-2)', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                    <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--crm-text-3)', flex: '0 0 auto' }} />
                    {actionLabel(v)}
                  </span>
                )
              }
              return (
                <Tag size="small" type="light" color={actionTone(v)}>
                  {actionLabel(v)}
                </Tag>
              )
            },
          },
          {
            // 来源：只显示中文，不再印 `WEB`/`AGENT` 这种码
            title: '来源',
            dataIndex: 'source',
            width: 85,
            // 来源全部是「网页操作」，信息量最低、也不需要一眼分辨：
            // 压成浅灰小字让位给动作列，页面上的颜色才集中在真正要看的那一列。
            render: (v: string | null) => (
              <span style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
                {v ? ENUM_LABEL[v] ?? v : '-'}
              </span>
            ),
          },
          {
            // 变更内容：摘要给人看，**原文不能丢** —— 这一页是合规取证用的。
            // 翻不出来的字段不硬凑英文名，而是给一个"看原始数据"的入口。
            //
            // 必须写死宽度：这一列原来不写 `width`、靠自适应吃剩余空间，
            // 内容短的时候没事，一旦摘要变长（「报价状态 草稿；币种 人民币；…」），
            // 列宽就跟着内容一起长，**整张表被撑出卡片右边界**。
            title: '变更内容',
            // 280 → 270：对象列加宽后总宽 929、容器 923，多出来的 5px 会在表底下
            // 留一条横向滚动条。往下压 10px 后落在 919，留 4px 余量给纵向滚动条。
            width: 270,
            render: (_: unknown, record: AuditLogRow) => {
              const summary = auditChangeSummary(record)
              const hidden = auditHiddenFieldCount(record)
              const raw = record.after_data ?? record.before_data
              return (
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                  {/* 翻不出中文摘要就留一个短横，**不要写「无变更」** ——
                      「客户接受」「中标」这类记录的快照里只有 `version_id`、`order_id`
                      这种内部外键，翻译不出中文，但它们并不是"没改东西"。
                      写「无变更」再接一个「还有 1 项」，就是当场自相矛盾。
                      真要看原文，右边的「明细」按钮一直在。 */}
                  {/* 摘要可能很长（「报价状态 草稿；币种 人民币；负责人 2；…」），
                      单行省略、把「明细」按钮固定在右边 —— 否则内容一长，
                      按钮就被挤出可见区，等于没有入口看原文。 */}
                  {/* 摘要按列宽物理截断（约两行 40 字），不用 CSS 省略号兜：
                      表格是 table-layout:auto，CSS 的 overflow/ellipsis 管不住
                      "这一列最少要多宽"，长句照样把表撑到 1399px（真翻过车，
                      body 得横向拖才能看全）。截断后列宽就真的落在 280 里了；
                      完整摘要挂 title，原文在「明细」里。 */}
                  <span
                    style={{
                      flex: 1,
                      minWidth: 0,
                      color: summary ? undefined : 'var(--crm-text-3)',
                      // Semi 的表格单元格默认是 nowrap，子元素不显式打开换行的话，
                      // 这一格会按"整句不换行"的自然宽度去参与布局 —— 列宽直接被
                      // 顶到 622px。这三行就是用来盖掉单元格那条 nowrap 的。
                      whiteSpace: 'normal',
                      wordBreak: 'break-word',
                      overflowWrap: 'anywhere',
                      // 统一压成两行：三行会把行高参差不齐，一屏能看的条数也变少。
                      // 40 字保底 + 两行封顶，两种保险都留着。
                      display: '-webkit-box',
                      WebkitBoxOrient: 'vertical',
                      WebkitLineClamp: 2,
                      overflow: 'hidden',
                    }}
                    title={summary ?? undefined}
                  >
                    {summary ? truncateSummary(summary) : '—'}
                  </span>
                  {/* 「还有 N 项」只在**已经有一句摘要**时才挂；
                      没有摘要的时候它只制造矛盾，不提供信息。 */}
                  {summary && hidden > 0 && <Tag size="small" color="grey">{`还有 ${hidden} 项`}</Tag>}
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
        width={520}
      >
        {detail && (
          <div style={{ display: 'grid', gap: 12 }}>
            <Typography.Text type="tertiary">
              {`${formatBeijingTime(detail.created_at)} · ${
                detail.operator_name ?? '系统'
              } · ${auditTypeLabel(detail.business_type)}${
                objectLabel(detail) ? ` ${objectLabel(detail)}` : ''
              } · ${actionLabel(detail.action)}`}
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
