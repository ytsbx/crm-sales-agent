import PersonalProfilePanel from './PersonalProfilePanel'
import UnratedRulesPanel from './UnratedRulesPanel'
import AutoTaskRulesPanel from './AutoTaskRulesPanel'
import { customerLevelLabel } from '../../shared/customer-level'
import { paymentCents } from '../../shared/payment-allocation'
import { useState, useRef, type CSSProperties } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Dropdown, Table, Tag, Typography } from '@douyinfe/semi-ui'
import {
  IconApartment, IconBell, IconBookmark, IconEditStroked, IconHistory, IconIdCard,
  IconList, IconSetting, IconUser, IconUserGroup,
} from '@douyinfe/semi-icons'

import {
  archiveUser,
  createDepartment,
  createRole,
  createUser,
  deleteDepartment,
  deleteRole,
  deleteUnusedUser,
  disableUser,
  enableUser,
  listDepartments,
  listPermissions,
  listRoles,
  listUsers,
  updateDepartment,
  updateRole,
  updateUser,
} from '../../shared/api/system'
import { createTag, deleteTag, listTags, updateTag, type TagRow } from '../../shared/api/customer'
import { ApiError } from '../../shared/api/client'
import {
  alignDingTalkDepartments,
  listOaMembers,
  provisionOaUser,
  setOaBinding,
  syncDingTalkUsers,
  type DepartmentAlignResult,
  type OaMember,
  type DingTalkSyncResult,
  type DingTalkUnmatched,
} from '../../shared/api/dingtalk'
import MaskedContact from '../../shared/components/MaskedContact'
import { useNotePrompt } from '../../shared/components/NotePromptModal'
import PageHeader from '../../shared/components/PageHeader'
import PermissionPicker from '../../shared/components/PermissionPicker'
import {
  listPublicPoolRules,
  listDictionaries,
  createDictionary,
  updateDictionary,
  listSettings,
  listTaskRules,
  runAutoTaskRules,
  runPublicPoolRecycle,
  saveSetting,
  updatePublicPoolRule,
  updateTaskRule,
  type PublicPoolRuleRow,
  type SystemSettingRow,
  type TaskRuleRow,
  type DictionaryItemRow,
} from '../../shared/api/settings'
import { Button, Input, Modal, Popconfirm, Select, Switch, TextArea, Toast } from '@douyinfe/semi-ui'
import { usePermissions } from '../../shared/hooks/permissions'
import type { SystemDepartment, SystemRole, SystemUser } from '../../shared/api/system'
import AuditLogPanel from './AuditLogPanel'
import NotificationSettingsPanel from './NotificationSettingsPanel'
import { emptyTextNode } from '../../shared/components/EmptyState'
import FormLabel from '../../shared/components/FormLabel'
import StatusDot from '../../shared/components/StatusDot'
import type { TagTone } from '../../shared/types'
import { settingDescription, settingLabel, settingNote } from '../../shared/setting-labels'

const TAG_GROUP_LABELS: Record<string, string> = { custom: '自定义', region: '区域', industry: '行业', grade: '等级', channel: '渠道' }

const PROFILE_TAB = { tab: '我的', itemKey: 'profile', icon: IconIdCard, description: '查看个人资料和权限，修改登录名或密码。' }
const TABS = [
  { tab: '用户', itemKey: 'users', permission: 'user:manage', icon: IconUser, description: '维护员工账号，分配部门和角色。' },
  { tab: '角色与数据范围', itemKey: 'roles', permission: 'user:manage', icon: IconUserGroup, description: '设置各岗位能做什么、能查看哪些数据。' },
  { tab: '部门', itemKey: 'departments', permission: 'user:manage', icon: IconApartment, description: '维护部门及上下级关系。' },
  { tab: '客户标签', itemKey: 'tags', permission: 'customer:update', icon: IconBookmark, description: '维护客户详情中可选的标签。' },
  { tab: '数据字典', itemKey: 'dictionaries', permission: 'settings:manage', icon: IconList, description: '维护业务表单可选择的客户等级。' },
  { tab: '业务规则', itemKey: 'rules', permission: 'settings:manage', icon: IconSetting, description: '在这里设置公海回收、待办提醒、报价和其他业务规则。' },
  { tab: '通知', itemKey: 'notifications', permission: 'settings:manage', icon: IconBell, description: '设置通知渠道和发送方式，查看企微送达情况。' },
  { tab: '审计日志', itemKey: 'audit', permission: 'audit:view', icon: IconHistory, description: '查看谁在什么时候做了哪些操作。' },
]

/**
 * 系统配置「值」这一列的字段形态（2026-11-19）。
 *
 * 原来所有配置项共用同一个裸 Input：30 是什么？0.15 是 15% 还是 0.15%？
 * CNY 该填什么？"未分级怎么处理"填 mode 还是中文？全靠猜。
 * 表格里 13 行长得一模一样，也确实"草率"。
 *
 * 这里按 key 给每项定死：单位、输入类型、可选项。配置项增删时跟着改这张表。
 */
type SettingFieldMeta = {
  /** 数字后面的单位：天 / 分 / 行 / kg/m³ */
  unit?: string
  /** percent：库里存的是小数（0.15），界面上写 15%，提交时换算回去 */
  kind?: 'text' | 'number' | 'percent' | 'select' | 'multiline' | 'object'
  options?: Array<{ value: string; label: string }>
  placeholder?: string
  /** 一句话交代"这个值怎么填"，挂在输入框下面 */
  hint?: string
  /** 常见取值：点开就能选，不必敲键盘（天数、百分比这类） */
  presets?: number[]
  /** 数值范围（前端先拦一道，别等后端 422 才说填错了） */
  min?: number
  max?: number
}

/**
 * 系统配置的分组（2026-11-19）。
 *
 * 原来 29 项平铺在一张表里：没有分类，从"公司名称"一路排到"通知重试"；
 * 每行一个占满整格的大输入框，说明挤在最右边一列 —— 主人原话
 * 「很乱，根本没有分类，还有一个框改一个设置，没有丝毫美感」。
 *
 * 分组按**业务域**切：改报价的人不用滚过客户沉睡天数。
 * 顺序也是信息：公司与单据 → 报价定价 → 客户节奏 → 公海 → 审批 → 通知导出。
 * 这里只放分组，字段形态仍在 SETTING_FIELD_META。
 */
const SETTING_GROUPS: Array<{
  id: string
  title: string
  note?: string
  /** 每个业务域一个色相：只在标题前那 6px 圆点、和顶部药丸上用它，
      整页还是白 + 发丝线 —— 有颜色，但不"花"。 */
  color: string
  /** 组尾的一句"这组影响什么"：不是填充文案，是把卡片底部收住的那一行。 */
  hint: string
  keys: string[]
}> = [
  {
    id: 'company',
    hint: '合同请到「文档管理」上传 Word 或 PDF 原稿。已上传的合同按原文件保留。',
    title: '公司与单据',
    color: '#2f6df6',
    note: '设置单据抬头和新报价的默认条款',
    // 2026-11-19 主人：「币种和贸易可以直接删，只会是人民币，只会在国内，别搞那么复杂」。
    // 这两项从页面摘掉（后端 key 仍留着，值恒为 CNY / domestic，业务单据按它算）。
    keys: ['company_name', 'default_payment_terms', 'default_delivery_terms', 'quote_valid_days'],
  },
  {
    id: 'pricing',
    hint: '核价时按这里算；角色专属规则优先于这里的默认值。',
    title: '报价与定价',
    color: '#6b5ce7',
    note: '核价时的默认口径；绝对底价是最后一道闸',
    keys: ['default_min_margin', 'default_target_margin', 'price_range_ratio', 'hard_floor'],
  },
  {
    id: 'customer',
    hint: '这里设置客户活跃、沉睡和商机预警的判断天数，以及客户查重阈值。生成待办使用「自动任务」里单独设置的天数。',
    title: '客户与商机节奏',
    color: '#12b5a0',
    note: '多少天没动静算沉睡 / 停滞，查重多少分算重复',
    keys: ['customer_active_days', 'customer_stale_days', 'dedup_scoring', 'opportunity_risk_days', 'opportunity_stale_days'],
  },
  // 「审批与外部系统」这一组删掉了（2026-11-19 主人：「是根本就不需要显示呀，
  // 显示这个给用户看做什么」）。组里只有同序 OA 三个模板编号（PROC-…），
  // 它们是接入后台的东西，业务管理员既看不懂也用不上；配错了反而会"看着配好了、
  // 实际发不出单"。报价审批分级在「报价与定价」单独编辑。
  {
    id: 'notify',
    hint: '这两项是安全阈值和计费口径，改由实施来定；页面上只读，值随时看得见。',
    title: '导出与物流',
    color: '#00b42a',
    /* 2026-11-19 主人：「按你建议恢复」—— 组回来了，但两行改只读：
       export 是防数据外泄的闸门（单次最多 5000 行、24 小时超 2 万行记审计告警），
       让管理员在页面上调大它等于拆闸门；logistics_volumetric_ratio 是物流计费
       口径（体积重系数，0 = 不折算）。两个都只展示、不提供编辑入口，
       真要改走实施（后端 PATCH /api/v1/settings）。 */
    note: '导出上限与告警阈值、物流体积重系数；只读展示，改动由实施处理',
    keys: ['export', 'logistics_volumetric_ratio'],
  },
]

/**
 * 「业务规则」页顶部的药丸切换（2026-11-19）。
 *
 * 这一页原来是把九块内容从下往上摞：公海回收规则 → 回收节奏 → 指路条 →
 * 自动任务 → 系统配置六组，一屏滚到底、白卡接白卡。主人说"布局设计质感都不行"。
 * 现在每次只显示一块：先选"改哪件事"，再改。
 *
 * 药丸上的那一点颜色就是该组的色相（和组标题前的圆点同一个色），
 * 选中时用该色 8% 的淡底 —— 有颜色，但整页仍是白底。
 */
const GROUP_TAB_LABEL: Record<string, string> = {
  customer: '客户与商机',
}

const RULE_PANELS = [
  { key: 'unrated', label: '未分级客户' },
  { key: 'pool', label: '公海回收' },
  { key: 'auto', label: '自动任务' },
  ...SETTING_GROUPS.map((g) => ({
    key: `g-${g.id}`,
    label: GROUP_TAB_LABEL[g.id] ?? g.title,
  })),
]

const SETTINGS_SEARCH = [
  ...[PROFILE_TAB, ...TABS].map((tab) => ({
    label: tab.tab, description: tab.description, place: '系统设置', to: `/settings?tab=${tab.itemKey}`,
  })),
  ...RULE_PANELS.map((panel) => ({
    label: panel.label,
    description: {
      unrated: '设置未分级客户的提醒与处理方式。',
      pool: '设置公海回收、未跟进天数、预告期和暂缓期。',
      auto: '设置报价未跟进和应收即将到期的待办提醒。',
    }[panel.key] ?? SETTING_GROUPS.find((group) => `g-${group.id}` === panel.key)?.note ?? '',
    place: '业务规则',
    to: `/settings?tab=rules&section=${panel.key}`,
  })),
  ...SETTING_GROUPS.flatMap((group) => group.keys.map((key) => ({
    label: key === 'dedup_scoring' ? '客户查重阈值' : settingLabel(key),
    description: settingDescription(key),
    place: `业务规则 / ${GROUP_TAB_LABEL[group.id] ?? group.title}`,
    to: `/settings?tab=rules&section=g-${group.id}`,
  }))),
  ...['pool_recycle_notice_days', 'pool_recycle_defer_days', 'pool_recycle_max_protect_days'].map((key) => ({
    label: settingLabel(key), description: settingDescription(key), place: '业务规则 / 公海回收',
    to: '/settings?tab=rules&section=pool',
  })),
  {
    label: '恢复公海客户', description: '在角色里选择哪些岗位有权恢复已回收的客户。',
    place: '角色与数据范围', to: '/settings?tab=roles',
  },
  {
    label: '企业微信提醒怎么发', description: '选择逐条即时推送或每天汇总发送。',
    place: '通知', to: '/settings?tab=notifications',
  },
]

const SETTING_FIELD_META: Record<string, SettingFieldMeta> = {
  company_name: { kind: 'text', placeholder: '例如：杭州天初科技有限公司' },
  customer_active_days: { unit: '天', presets: [7, 15, 30] },
  customer_stale_days: { unit: '天', presets: [30, 60, 90] },
  opportunity_risk_days: { unit: '天', presets: [7, 14, 30] },
  opportunity_stale_days: { unit: '天', presets: [15, 30, 60] },
  quote_valid_days: { unit: '天', presets: [7, 15, 30, 60] },
  pool_recycle_defer_days: { unit: '天', presets: [15, 30, 60], min: 0, max: 365 },
  pool_recycle_notice_days: { unit: '天', presets: [0, 3, 7, 15], min: 0, max: 365 },
  pool_recycle_max_protect_days: { unit: '天', presets: [30, 60, 90] },
  default_min_margin: { kind: 'percent', hint: '填 15 表示最低毛利 15%' , presets: [10, 15, 20, 25] },
  default_target_margin: { kind: 'percent', hint: '填 30 表示目标毛利 30%' , presets: [20, 25, 30, 35] },
  price_range_ratio: { kind: 'percent', hint: '报价区间宽度，填 4 表示 ±4%' , presets: [3, 4, 5] },
  hard_floor: {
    kind: 'select',
    options: [
      { value: 'off', label: '关闭绝对底价' },
      { value: 'cost', label: '不得低于成本' },
      { value: 'cost_markup', label: '不得低于成本加成价' },
    ],
  },
  default_delivery_terms: { kind: 'multiline', placeholder: '新报价默认带上的交货与运费说明' },
  default_payment_terms: { kind: 'text', placeholder: '例如：款到发货' },
  /** 权限码这一项：选项在页面里按权限清单现取，所以这里只声明"是选择类" */
  logistics_volumetric_ratio: { unit: 'kg/m³', hint: '体积重换算系数，0 表示不折算' },
  dedup_scoring: { unit: '分', min: 0, max: 100, hint: '总分达到这个分数时提示疑似重复' },
  export: { unit: '行', hint: '单次导出上限；超阈值提醒在下面几项单独配' },
  dingtalk_oa: { kind: 'object' },
  dingtalk_sample_oa: { kind: 'object' },
  dingtalk_contract_oa: { kind: 'object' },
  notification_channels: { kind: 'object' },
  notification_levels: { kind: 'object' },
  notification_retry: { kind: 'object' },
  approval_levels: { kind: 'object' },
}

/** 库里的小数比例 ↔ 界面上的整数百分比。0.15 → "15"，填 "15" → 0.15。 */
function ratioToText(value: unknown): string {
  const num = Number(value)
  if (!Number.isFinite(num)) return ''
  return String(Math.round(num * 1000) / 10)
}

type SettingSaveStatus = 'idle' | 'dirty' | 'saving' | 'saved' | 'error'

/**
 * 一个配置项的「值」：输入框 + 单位 + 保存状态。
 *
 * 三件事必须让用户看见：
 * 1) 单位 —— 30 是 30 天，不是 30 个；
 * 2) 未保存 / 已保存 —— 原来是"移开焦点就静默提交 + 全表弹一个 toast"，
 *    改了哪一行、存没存上，心里没数；
 * 3) 填错了 —— 数字项填了汉字当场标红，不要等提交回来才报错。
 */
/**
 * 「这一项到底配没配」的判据。
 *
 * 不能数对象里有几个 key：同序 OA 的配置对象里 `field_map`、`content_component`
 * 这些空壳字段永远存在，数出来是"已配置 10 项"；反过来只挑一个标量看，
 * 又会把配好的项判成空。所以按业务上的"关键字段"逐项判：
 * 同序 OA 三件看审批模板编号（process_code），有编号才算接通。
 */
function objectConfigInfo(key: string, value: unknown): { configured: boolean; detail: string; detailFull?: string } {
  const raw = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>
  if (key === 'dingtalk_oa' || key === 'dingtalk_contract_oa' || key === 'dingtalk_sample_oa') {
    const code = String(raw.process_code ?? '').trim()
    if (!code) return { configured: false, detail: '' }
    const mapCount = raw.field_map && typeof raw.field_map === 'object'
      ? Object.keys(raw.field_map as Record<string, unknown>).length
      : 0
    const owners = Array.isArray(raw.quote_owners) ? raw.quote_owners.length : 0
    const bits = [mapCount ? `映射 ${mapCount} 项` : '', owners ? `发起人 ${owners} 人` : ''].filter(Boolean)
    return {
      configured: true,
      detail: bits.length ? bits.join(' · ') : '模板已接',
      detailFull: `${code}${bits.length ? ` · ${bits.join(' · ')}` : ''}`,
    }
  }
  const count = Array.isArray(value) ? value.length : Object.keys(raw).length
  return { configured: count > 0, detail: `${count} 项` }
}


function SettingValueField({
  rowKey,
  field,
  value,
  markupRatio,
  onSave,
}: {
  rowKey: string
  field: string
  value: unknown
  markupRatio?: unknown
  onSave: (next: Record<string, unknown>) => Promise<unknown>
}) {
  const meta = SETTING_FIELD_META[rowKey] ?? {}
  const kind = meta.kind ?? 'number'
  const choices = meta.options
  const isHardFloor = rowKey === 'hard_floor'
  const initial = kind === 'percent' ? ratioToText(value) : value === null || value === undefined ? '' : String(value)
  const currentMarkup = Number(markupRatio ?? 0)
  const initialMarkup = (currentMarkup * 100).toLocaleString('en-US', { useGrouping: false, maximumFractionDigits: 15 })
  const [text, setText] = useState(initial)
  const [markup, setMarkup] = useState(initialMarkup)
  const [status, setStatus] = useState<SettingSaveStatus>('idle')
  /** 静止态 / 编辑态：页面静止时只有一个值，点开才长控件 */
  const [open, setOpen] = useState(false)
  /** 有预设取值的项：默认只给胶囊，点「自定义」才露出输入框 */
  const [custom, setCustom] = useState(false)

  const editsMarkup = isHardFloor && text === 'cost_markup'
  const markupPercent = Number(markup)
  const validMarkup = markup.trim() !== '' && Number.isFinite(markupPercent) && markupPercent >= 0 && markupPercent <= 100
  const markupChanged = editsMarkup && markup !== initialMarkup

  const finish = (next: SettingSaveStatus) => {
    setStatus(next)
    if (next === 'saved') window.setTimeout(() => setStatus((current) => (current === 'saved' ? 'idle' : current)), 1400)
  }

  const commit = async (raw: string) => {
    if (status === 'saving') return
    let parsed: unknown = raw
    if (kind === 'percent') {
      const num = Number(raw)
      if (!raw.trim() || !Number.isFinite(num) || (meta.min !== undefined && num < meta.min)) {
        finish('error')
        return
      }
      parsed = num / 100
    } else if (kind === 'number') {
      if (raw.trim() === '') { setStatus('error'); return }
      const num = Number(raw)
      if (!Number.isFinite(num)) { setStatus('error'); return }
      if (meta.min !== undefined && num < meta.min) { setStatus('error'); return }
      if (meta.max !== undefined && num > meta.max) { setStatus('error'); return }
      parsed = num
    }
    if (editsMarkup && !validMarkup) return
    if (raw === initial && !markupChanged) { setOpen(false); setStatus('idle'); return }
    const next = { [field]: parsed }
    if (editsMarkup) next.markup_ratio = markupChanged ? markupPercent / 100 : currentMarkup
    setStatus('saving')
    try {
      await onSave(next)
      setOpen(false)
      finish('saved')
    } catch {
      finish('error')
    }
  }

  if (kind === 'object') {
    const info = objectConfigInfo(rowKey, value)
    // "已配置 0 项"自相矛盾（同序 OA 这几项的字段映射就是空对象）：
    // 没配就写"未配置"，并明确"在哪配"，别让人以为这行坏了。
    if (!info.configured) {
      return (
        <div className="setting-object-value">
          <Tag color="orange" size="small">未配置</Tag>
          <span>在下方对应区块填写后生效</span>
        </div>
      )
    }
    return (
      <div className="setting-object-value">
        <Tag color="blue" size="small">已配置</Tag>
        <span title={info.detailFull}>{info.detail}</span>
      </div>
    )
  }

  const readText = () => {
    if (isHardFloor && initial === 'cost_markup') return `成本加成 ${initialMarkup}%`
    if (kind === 'select') return choices?.find((option) => option.value === initial)?.label ?? '当前值待核对'
    return `${initial}${kind === 'percent' ? '%' : meta.unit ?? ''}`
  }
  const cancel = () => {
    setText(initial)
    setOpen(false)
    setCustom(false)
    setStatus('idle')
  }

  if (!open) {
    const empty = kind !== 'select' && initial.trim() === ''
    return (
      <div className="setting-value-cell">
        <button
          type="button"
          className={`setting-value-view${empty ? ' is-empty' : ''}`}
          aria-label={`修改${rowKey === 'dedup_scoring' ? '客户查重阈值' : settingLabel(rowKey)}`}
          onClick={() => {
            setText(initial)
            setMarkup(initialMarkup)
            setOpen(true)
            setStatus('dirty')
          }}
        >
          <span className="setting-value-text">{empty ? '未填写' : readText()}</span>
          <span className="setting-value-edit"><IconEditStroked size="small" aria-hidden="true" />修改</span>
        </button>
        {status === 'saved' && <span className="setting-save-state is-saved">已保存</span>}
      </div>
    )
  }

  const actions = (
    <div className="setting-value-actions">
      <Button size="small" theme="outline" disabled={kind === 'select' && (
        !text || (text === initial && !markupChanged) || (editsMarkup && !validMarkup)
      )} loading={status === 'saving'} onClick={() => void commit(text)}>
        保存
      </Button>
      <Button size="small" theme="borderless" disabled={status === 'saving'} onClick={cancel}>
        取消
      </Button>
      {status === 'error' && <span className="setting-save-state is-error">未保存，请核对后重试</span>}
    </div>
  )

  if (kind === 'select') {
    return (
      <div className="setting-value-cell is-editing">
        <div className="setting-option-chips">
          {choices?.map((option) => (
            <button
              type="button"
              key={option.value}
              className={`setting-option-chip${option.value === text ? ' is-on' : ''}`}
              aria-pressed={option.value === text}
              disabled={status === 'saving'}
              onClick={() => { setText(option.value); setStatus('dirty') }}
            >
              {option.label}
            </button>
          ))}
        </div>
        {editsMarkup && (
          <div className="setting-markup-editor">
            <label htmlFor="setting-hard-floor-markup">成本加成比例</label>
            <Input
              id="setting-hard-floor-markup"
              inputMode="decimal"
              suffix="%"
              value={markup}
              disabled={status === 'saving'}
              aria-invalid={!validMarkup}
              onChange={(next) => { setMarkup(next); setStatus('dirty') }}
            />
            <div className="settings-help">
              {validMarkup
                ? `成本 100 元时，绝对底价为 ${(100 + markupPercent).toLocaleString('zh-CN', { maximumFractionDigits: 4 })} 元。`
                : '请输入 0 到 100 之间的百分比。'}
            </div>
          </div>
        )}
        {actions}
      </div>
    )
  }

  // 长文本：这里才真的需要一块文本域
  if (kind === 'multiline') {
    return (
      <div className="setting-value-cell is-editing">
        <TextArea
          // 定高 3 行，不用 autosize：autosize 的外层容器比可见框高出 40px，
          // 说明文字被顶出一条空白，看着像这一段没填。
          rows={3}
          value={text}
          placeholder={meta.placeholder}
          onChange={(next) => { setText(next); setStatus('dirty') }}
        />
        {actions}
      </div>
    )
  }

  // 数字 / 百分比 / 短文本：先给常见取值，想填别的点「自定义」
  // （主人 2026-11-19：「该有自定义的要能自定义」—— 原来胶囊旁边直接摆一个
  //   没说明的输入框，既不知道它是"兜底"，也说不清什么时候该用它。）
  const needCustom = !meta.presets || custom
  return (
    <div className="setting-value-cell is-editing">
      {meta.presets && (
        <div className="setting-preset-row">
          {meta.presets.map((preset) => (
            <button
              type="button"
              key={preset}
              className={`setting-preset${!custom && String(preset) === text ? ' is-on' : ''}`}
              onClick={() => {
                setCustom(false)
                setText(String(preset))
                setStatus('dirty')
              }}
            >
              {preset}
              {kind === 'percent' ? '%' : meta.unit ?? ''}
            </button>
          ))}
          {/* 预设只是"最常见的几个"，不是全部取值 —— 留一个明确的出口 */}
          <button
            type="button"
            className={`setting-preset is-custom${custom ? ' is-on' : ''}`}
            onClick={() => {
              setCustom(true)
              setStatus('dirty')
            }}
          >
            {custom ? '自定义中' : '自定义…'}
          </button>
        </div>
      )}
      {needCustom && (
        <div className="setting-value-row">
          <Input
            autoFocus={meta.presets ? true : undefined}
            className={meta.unit || kind === 'number' || kind === 'percent' ? 'setting-value-input is-num' : 'setting-value-input'}
            value={text}
            placeholder={meta.placeholder ?? (meta.min !== undefined ? `${meta.min}–${meta.max ?? '不限'}` : '填一个数')}
            suffix={meta.unit ?? (kind === 'percent' ? '%' : undefined)}
            onChange={(next) => { setText(next); setStatus('dirty') }}
            onEnterPress={() => void commit(text)}
          />
          {actions}
        </div>
      )}
      {meta.presets && !custom && (
        actions
      )}
    </div>
  )
}

function CustomerScoreDetails({ details }: { details?: SystemSettingRow['customer_score_details'] }) {
  return (
    <details className="customer-score-details">
      <summary>查看计分规则</summary>
      {details ? (
        <div className="customer-score-body">
          <div className="customer-score-groups">
            <section>
              <h5>客户名称</h5>
              <dl>
                <div><dt>名称一致</dt><dd>{details.points.weight_name_exact} 分</dd></div>
                <div><dt>一个名称含另一个</dt><dd>{details.points.weight_name_contains} 分</dd></div>
                <div><dt>名称部分相似</dt><dd>{details.points.weight_name_contains} 分 × 相似度</dd></div>
              </dl>
              <p>名称从上到下只取一项。部分相似时，用最长一段连续相同的字数除以较短名称的字数，达到 {Math.round(details.name_similarity_min * 100)}% 才计分，结果向下取整。</p>
            </section>
            <section>
              <h5>其他匹配项</h5>
              <dl>
                <div><dt>统一社会信用代码一致</dt><dd>{details.points.weight_tax_no} 分</dd></div>
                <div><dt>联系人手机号一致</dt><dd>{details.points.weight_mobile} 分</dd></div>
                <div><dt>官网域名一致</dt><dd>{details.points.weight_domain} 分</dd></div>
                <div><dt>地址一致</dt><dd>{details.points.weight_address} 分</dd></div>
              </dl>
            </section>
          </div>
          <p className="customer-score-total">各项得分相加，最高 {details.max_score} 分。当前达到 <strong>{details.points.threshold} 分</strong>就会提示疑似重复。</p>
        </div>
      ) : <p className="settings-help">暂时无法读取当前计分规则。</p>}
    </details>
  )
}

function oaMemberLabel(member: OaMember) {
  return [member.name, member.department || '未设部门', member.employee_number].filter(Boolean).join(' · ')
}

export default function SettingsPage() {
  /* 填原因的弹窗（2026-11-19）：原来这里三处都是 window.prompt，
     浏览器原生灰框、顶着"127.0.0.1:5275 显示"，没法做必填和字数提示。 */
  const notePrompt = useNotePrompt()
  // 支持深链：/settings?tab=rules
  const [searchParams, setSearchParams] = useSearchParams()
  const { can, isAdmin } = usePermissions()
  const canManageUsers = can('user:manage')
  const canManageSettings = can('settings:manage')
  const canManageTags = can('customer:view') && can('customer:update')
  const visibleTabs = [PROFILE_TAB, ...TABS.filter((item) =>
    item.itemKey === 'tags' ? canManageTags : can(item.permission),
  )]
  const hasManagementTabs = visibleTabs.length > 1
  const [settingSearch, setSettingSearch] = useState('')
  const searchTerms = settingSearch.trim().toLowerCase().split(/\s+/).filter(Boolean)
  const settingMatches = searchTerms.length > 0 ? SETTINGS_SEARCH.filter((entry) => {
    const tab = new URLSearchParams(entry.to.split('?')[1]).get('tab')
    if (!visibleTabs.some((item) => item.itemKey === tab)) return false
    const text = `${entry.label} ${entry.description} ${entry.place}`.toLowerCase()
    return searchTerms.every((term) => text.includes(term))
  }) : []
  // 个人账号向全部登录人开放；管理与规则配置按 settings:manage 开放。
  // 公海回收的**复核**已经搬成独立页（/pool-recycle），这里不再有复核页签。
  // 第一个页签改叫「我的」而不是「个人账号」：它现在不只有登录名/密码，
  // 还挂着「我的信息」和「我的权限」—— 叫「个人账号」的话，想看自己有哪些
  // 权限的人根本不会往这里点。
  const activeKey = visibleTabs.some((item) => item.itemKey === searchParams.get('tab'))
    ? searchParams.get('tab')! : (visibleTabs[1]?.itemKey ?? 'profile')
  const currentTab = visibleTabs.find((item) => item.itemKey === activeKey) ?? PROFILE_TAB

  // 设置页按「这是谁的事」分四组：个人 / 组织与人 / 业务配置 / 系统。
  // 组内一个可见页签都没有的组直接不渲染 —— 普通用户只看到「我的」，
  // 不会冒出三个空标题；只有公海复核权的人只多出「业务配置」一组。
  const tabGroups = [
    { id: 'personal', title: '个人', keys: ['profile'] },
    { id: 'organization', title: '组织与权限', keys: ['users', 'roles', 'departments'] },
    { id: 'business', title: '业务配置', keys: ['tags', 'dictionaries', 'rules'] },
    { id: 'system', title: '系统', keys: ['notifications', 'audit'] },
  ]
    .map((group) => ({
      ...group,
      items: visibleTabs.filter((item) => group.keys.includes(item.itemKey)),
    }))
    .filter((group) => group.items.length > 0)

  // 用户列表：搜索与筛选（后端 `GET /users` 本来就支持 keyword / status /
  // department_id / 分页，从前前端一个都没接 —— 只能靠肉眼在一屏里找人，
  // 而且**超过 50 人后面的人根本显示不出来**（没有翻页，静默截断）。
  //
  // 关键字分成两个状态：`userKeywordInput` 是输入框里的字、`userKeyword`
  // 是**已经提交给服务端**的那个（回车或点「查询」才更新）—— 否则每敲一个字
  // 就发一次请求，人还没打完列表已经翻腾好几轮了。
  // 业务规则子页由 URL 参数选中。
  const requestedPanel = searchParams.get('section')
  const panelKey = RULE_PANELS.some((panel) => panel.key === requestedPanel) ? requestedPanel! : 'pool'
  const [userKeywordInput, setUserKeywordInput] = useState('')
  const [userKeyword, setUserKeyword] = useState('')
  const [userStatus, setUserStatus] = useState<string>('')
  const [userDeptId, setUserDeptId] = useState<number | undefined>(undefined)
  const [userPage, setUserPage] = useState(1)
  const [userPageSize, setUserPageSize] = useState(20)
  // 任一条件变了都要回到第一页：翻到第 3 页再改条件，"第 3 页"多半已经不存在了
  const resetUserPage = () => setUserPage(1)
  const usersQuery = useQuery({
    queryKey: ['settings-users', userKeyword, userStatus, userDeptId, userPage, userPageSize],
    queryFn: () =>
      listUsers({
        keyword: userKeyword || undefined,
        status: userStatus || undefined,
        omit_archived: true,
        department_id: userDeptId,
        page: userPage,
        page_size: userPageSize,
      }),
    enabled: canManageUsers,
  })
  // 角色 / 部门：这两个接口一次就把全部数据返回了（角色就几个、部门通常也就几十个），
  // 后端没有搜索参数 —— 在页面上本地筛就够了，不必为它去改后端。
  const [roleKeyword, setRoleKeyword] = useState('')
  const [deptKeyword, setDeptKeyword] = useState('')
  const rolesQuery = useQuery({ queryKey: ['settings-roles'], queryFn: listRoles, enabled: canManageUsers })
  const deptsQuery = useQuery({
    queryKey: ['settings-departments'],
    queryFn: listDepartments,
    enabled: canManageUsers,
  })
  const queryClient = useQueryClient()
  const poolRulesQuery = useQuery({
    queryKey: ['public-pool-rules'],
    queryFn: listPublicPoolRules,
    enabled: canManageSettings && activeKey === 'rules',
  })
  const taskRulesQuery = useQuery({
    queryKey: ['task-rules'],
    queryFn: listTaskRules,
    enabled: canManageSettings && activeKey === 'rules',
  })
  const settingsQuery = useQuery({
    queryKey: ['system-settings'],
    queryFn: listSettings,
    enabled: canManageSettings && activeKey === 'rules',
  })
  const dictionariesQuery = useQuery({
    queryKey: ['settings-dictionaries'],
    queryFn: () => listDictionaries(undefined, false),
    enabled: canManageSettings && activeKey === 'dictionaries',
  })
  const [dictionaryForm, setDictionaryForm] = useState({
    type: 'customer_level', code: '', label: '', sort_no: '0', remark: '',
  })
  const settingRow = (key: string): SystemSettingRow | null =>
    (settingsQuery.data ?? []).find((row) => row.key === key) ?? null
  const noticeSetting = settingRow('pool_recycle_notice_days')
  const deferSetting = settingRow('pool_recycle_defer_days')
  const protectSetting = settingRow('pool_recycle_max_protect_days')

  // 客户标签字典（03-API §7 的 /tags）
  const tagsQuery = useQuery({
    queryKey: ['settings-tags'],
    queryFn: () => listTags(false),
    enabled: canManageTags && activeKey === 'tags',
  })
  const [tagForm, setTagForm] = useState<{ name: string; type: string }>({ name: '', type: 'custom' })
  const tagNameInput = useRef<HTMLInputElement>(null)
  const [tagCreateError, setTagCreateError] = useState('')
  const [tagEditTarget, setTagEditTarget] = useState<TagRow | null>(null)

  const refreshTags = () => void queryClient.invalidateQueries({ queryKey: ['settings-tags'] })

  const createTagMutation = useMutation({
    mutationFn: createTag,
    onSuccess: () => {
      Toast.success('标签已创建')
      setTagForm({ name: '', type: 'custom' })
      refreshTags()
      void queryClient.invalidateQueries({ queryKey: ['tags'] })
    },
    onError: (error: Error) => setTagCreateError(error.message),
  })

  const updateTagMutation = useMutation({
    mutationFn: () =>
      updateTag(tagEditTarget!.id, { name: tagEditTarget!.name, type: tagEditTarget!.type, status: tagEditTarget!.status }),
    onSuccess: () => {
      Toast.success('标签已保存')
      setTagEditTarget(null)
      refreshTags()
      void queryClient.invalidateQueries({ queryKey: ['tags'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const deleteTagMutation = useMutation({
    mutationFn: (id: number) => deleteTag(id),
    onSuccess: () => {
      Toast.success('标签已删除')
      refreshTags()
      void queryClient.invalidateQueries({ queryKey: ['tags'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const refreshRules = () => {
    void queryClient.invalidateQueries({ queryKey: ['public-pool-rules'] })
    void queryClient.invalidateQueries({ queryKey: ['task-rules'] })
    void queryClient.invalidateQueries({ queryKey: ['system-settings'] })
  }

  const poolRuleMutation = useMutation({
    mutationFn: ({ id, ...changes }: { id: number; days?: number; enabled?: boolean }) =>
      updatePublicPoolRule(id, changes),
    onSuccess: () => {
      Toast.success('公海规则已保存')
      refreshRules()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const taskRuleMutation = useMutation({
    mutationFn: ({ id, days }: { id: number; days: number }) => {
      const rule = (taskRulesQuery.data ?? []).find((item) => item.id === id)
      return updateTaskRule(id, {
        trigger_config: { ...(rule?.trigger_config ?? {}), days },
      })
    },
    onSuccess: (data) => {
      queryClient.setQueryData<TaskRuleRow[]>(['task-rules'], (rows) =>
        rows?.map((row) => row.id === data.id ? data : row),
      )
      Toast.success('自动任务规则已保存')
      refreshRules()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const taskRuleStatusMutation = useMutation({
    mutationFn: ({ id, active }: { id: number; active: boolean }) =>
      updateTaskRule(id, { status: active ? 'active' : 'inactive' }),
    onSuccess: (data) => {
      queryClient.setQueryData<TaskRuleRow[]>(['task-rules'], (rows) =>
        rows?.map((row) => row.id === data.id ? data : row),
      )
      Toast.success(data.status === 'active' ? '规则已启用' : '规则已停用')
      refreshRules()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const settingMutation = useMutation({
    mutationFn: ({ key, value }: { key: string; value: Record<string, unknown>; silent?: boolean }) =>
      saveSetting(key, value),
    onSuccess: (data, variables) => {
      // silent：系统配置表每一行都有自己的"已保存"提示，再弹一个全表 toast
      // 就变成"改一项弹一次"，反而看不清是哪行存的。
      if (!variables?.silent) Toast.success('配置已保存')
      queryClient.setQueryData<SystemSettingRow[]>(['system-settings'], (rows) =>
        rows?.map((row) => row.key === data.key ? data : row),
      )
      refreshRules()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const dictionaryMutation = useMutation({
    mutationFn: () => createDictionary({
      type: dictionaryForm.type,
      code: dictionaryForm.code.trim(),
      label: dictionaryForm.label.trim(),
      sort_no: Number(dictionaryForm.sort_no) || 0,
      remark: dictionaryForm.remark.trim() || undefined,
    }),
    onSuccess: () => {
      Toast.success('字典项已创建')
      setDictionaryForm((current) => ({ ...current, code: '', label: '', remark: '' }))
      void queryClient.invalidateQueries({ queryKey: ['settings-dictionaries'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const dictionaryUpdateMutation = useMutation({
    mutationFn: ({ id, changes }: { id: number; changes: Parameters<typeof updateDictionary>[1] }) => updateDictionary(id, changes),
    onSuccess: () => {
      Toast.success('字典项状态已保存')
      void queryClient.invalidateQueries({ queryKey: ['settings-dictionaries'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const renderSettingValue = (record: SystemSettingRow) => {
    const raw = record.value ?? {}
    // 这两项只读（2026-11-19 恢复该组时定的口径）：
    // 不是不给看，是不给在页面上改 —— 一个是防外泄的行数闸门，一个是物流计费口径，
    // 调错了要么导不出、要么算错运费，都由实施确认后再改。
    if (record.key === 'export') {
      const box = (raw ?? {}) as Record<string, unknown>
      const limit = Number(box.limit ?? 5000)
      const alertRows = Number(box.alert_rows ?? 20000)
      const windowHours = Number(box.alert_window_hours ?? 24)
      return (
        <span className="setting-readonly-value">
          单次最多导出 {limit.toLocaleString()} 行；{windowHours} 小时内导出超过{' '}
          {alertRows.toLocaleString()} 行会记一条审计告警
        </span>
      )
    }
    if (record.key === 'logistics_volumetric_ratio') {
      const ratio = Number(raw?.number ?? 0)
      return (
        <span className="setting-readonly-value">
          {ratio === 0 ? '未启用 —— 计费重量只按实际重量算' : `1 立方米按 ${ratio} kg 折算`}
        </span>
      )
    }
    if (record.key === 'approval_levels' && Array.isArray(raw.levels)) {
      // 审批分档改成"就地编辑表格"（2026-10-14 主人拍板）。
      // 旧写法是一张只读小表格 + 一个让你**手写整段 JSON** 的输入框：
      // 同一份数据画两遍，想改还得自己数括号和引号，错一个字符整条不生效，
      // 而且只弹一句提示、写的东西还不退回去。现在表格本身可改。
      //
      // key 挂在数据上：保存成功后 /settings 回来新数据 → 组件重建、显示服务器
      // 真值；用户正在编辑时 props 不变、不重建，免得把没提交的改动冲掉。
      return (
        <ApprovalLevelsEditor
          key={JSON.stringify(raw)}
          levels={raw.levels as Array<Record<string, unknown>>}
          saving={settingMutation.isPending}
          onSave={(levels) => settingMutation.mutate({ key: record.key, value: { levels } })}
        />
      )
    }
    // 一条配置的值可能是个对象（threshold + 若干权重、limit + 提醒阈值、
    // 同序 OA 的模板号 + 字段映射）。取第一个标量来就地编辑，其余交给
    // 专门的区块（「客户查重规则」「同序 OA 审批配置」）；
    // 整个都是对象的那几个（通知渠道 / 重试策略）则由组件渲染成"已配置 N 项"。
    // ⚠️ kind === 'object' 的项必须把**整个对象**传下去。
    // 以前这里统一"取第一个标量字段"再传，而 object 那几项（同序 OA 三件）
    // 传下去的其实是一串 process_code —— 组件拿字符串去数 key，永远是 0 项，
    // 于是明明配好了模板和字段映射，页面上却写「未配置」（2026-11-19 主人问：
    // "这个我没配置吗"，实测后端有 process_code + field_map + 发起人）。
    if ((SETTING_FIELD_META[record.key]?.kind) === 'object') {
      return (
        <SettingValueField
          rowKey={record.key}
          field={record.key}
          value={raw}
          onSave={(next) => settingMutation.mutateAsync({ key: record.key, value: next, silent: true })}
        />
      )
    }
    const scalarKey = (record.key === 'hard_floor' ? 'mode' : record.key === 'dedup_scoring' ? 'threshold' : undefined) ?? Object.keys(raw).find((name) => {
      const candidate = raw[name]
      return candidate === null || candidate === undefined || typeof candidate !== 'object'
    }) ?? Object.keys(raw)[0] ?? 'text'
    return (
      <SettingValueField
        rowKey={record.key}
        field={scalarKey}
        value={record.key === 'dedup_scoring'
          ? record.customer_score_details?.points.threshold ?? raw[scalarKey]
          : raw[scalarKey]}
        markupRatio={record.key === 'hard_floor' ? raw.markup_ratio : undefined}
        onSave={(next) => settingMutation.mutateAsync({ key: record.key, value: next, silent: true })}
      />
    )
  }

  const recycleMutation = useMutation({
    mutationFn: runPublicPoolRecycle,
    onSuccess: (data) => {
      Toast.success(data.nominated_count ? `扫描完成，新生成 ${data.nominated_count} 条回收预告` : '扫描完成，没有新增回收预告')
      void queryClient.invalidateQueries({ queryKey: ['recycle-candidates'] })
      refreshRules()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  const poolScan = recycleMutation.data
  const poolScanReasons = poolScan ? [
    { count: poolScan.cooldown_count, label: '距离上次驳回未满 30 天' },
    { count: poolScan.already_open_count, label: '已有回收复核记录，等待处理' },
    { count: poolScan.protected_count, label: '仍在履约保护期' },
    { count: poolScan.disputed_count, label: '撞单争议待裁定' },
    { count: poolScan.unknown_contact_count, label: '最近联系时间待核实' },
    { count: poolScan.wecom_unfollowed_count, label: '企业微信导入未跟进（暂不回收）' },
  ].filter(({ count }) => (count ?? 0) > 0) : []

  const autoTaskMutation = useMutation({
    mutationFn: runAutoTaskRules,
    onSuccess: (data) => {
      if (data.failed_rule_count) {
        Toast.warning(`生成 ${data.created_count} 条任务；${data.failed_rule_count} 条规则配置有误：${data.rule_errors?.map((row) => `${row.code}：${row.error}`).join('；') ?? '请查看审计记录'}`)
      } else {
        Toast.success(`已执行，生成 ${data.created_count} 条自动任务`)
      }
      refreshRules()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  // ---- 用户 / 角色 / 部门 的增删改（03-API §3 / §4 / §5） ----
  // 同序 OA 同步的结果要**逐条看得见**（谁没对上、谁还没绑），所以用弹窗而不是一句话提示：
  // 对不上的人是要管理员去处理的，一闪而过的提示等于没给。
  const [dtSyncVisible, setDtSyncVisible] = useState(false)
  const [dtSyncResult, setDtSyncResult] = useState<DingTalkSyncResult | null>(null)
  const [userModal, setUserModal] = useState(false)
  const [userEditTarget, setUserEditTarget] = useState<SystemUser | null>(null)
  const [userForm, setUserForm] = useState<{
    name: string
    username: string
    password: string
    mobile: string
    email: string
    department_id?: number
    role_ids: number[]
    /** 选中的同序身份；有值时按同序通讯录建账号，姓名/部门由后端带入 */
    oa_user_id?: string
    /** 仅用于界面展示同序部门 */
    oa_department?: string
  }>({ name: '', username: '', password: '', mobile: '', email: '', role_ids: [] })

  const [oaBindingTarget, setOaBindingTarget] = useState<SystemUser | null>(null)
  const [selectedOaMember, setSelectedOaMember] = useState<string | null>(null)

  const [roleModal, setRoleModal] = useState(false)
  const [roleEditTarget, setRoleEditTarget] = useState<SystemRole | null>(null)
  const [roleForm, setRoleForm] = useState<{
    code: string
    name: string
    description: string
    data_scope: string
    /** 启用 / 停用。停用后该角色不再参与权限、角色特例与数据范围计算（第十批 10.10）。 */
    status: string
    permission_codes: string[]
  }>({ code: '', name: '', description: '', data_scope: 'self', status: 'active', permission_codes: [] })

  const [deptModal, setDeptModal] = useState(false)
  const [deptEditTarget, setDeptEditTarget] = useState<SystemDepartment | null>(null)
  const [deptForm, setDeptForm] = useState<{
    name: string
    parent_id?: number
    wecom_department_id: string
  }>({ name: '', wecom_department_id: '' })

  const permissionsQuery = useQuery({
    queryKey: ['settings-permissions'],
    queryFn: listPermissions,
    enabled: roleModal,
  })

  // 新建用户时可选同序身份：只在新建弹窗打开时读通讯录，避免每次进页面都拉。
  const oaMembersQuery = useQuery({
    queryKey: ['settings-oa-members'],
    queryFn: listOaMembers,
    enabled: isAdmin && ((userModal && !userEditTarget) || Boolean(oaBindingTarget)),
    staleTime: 60_000,
  })

  // 列表刷新统一走这里：**先取消在途请求再失效**，否则旧请求晚回来会把新数据盖掉。
  const refreshUsers = () => {
    void queryClient.cancelQueries({ queryKey: ['settings-users'] })
    void queryClient.invalidateQueries({ queryKey: ['settings-users'] })
  }
  const refreshRoles = () => {
    void queryClient.invalidateQueries({ queryKey: ['settings-roles'] })
    void queryClient.invalidateQueries({ queryKey: ['assignable-users'] })
  }
  const refreshDepts = () => void queryClient.invalidateQueries({ queryKey: ['settings-departments'] })

  const oaBindingMutation = useMutation({
    mutationFn: ({ userId, oaUserId }: { userId: number; oaUserId: string | null }) =>
      setOaBinding(userId, oaUserId),
    onSuccess: (result) => {
      if (result.department_synced) Toast.success('同序绑定和部门已同步')
      else if (result.oa_user_id) Toast.info('已绑定同序。同序未设置部门，CRM 部门保留原值')
      else Toast.success('已解除同序绑定')
      setOaBindingTarget(null)
      refreshUsers()
      refreshDepts()
      void queryClient.invalidateQueries({ queryKey: ['settings-oa-members'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const userSaveMutation = useMutation<{ id: number }, Error, void>({
    mutationFn: () => {
      if (!userEditTarget && userForm.oa_user_id) {
        // 选了同序身份：姓名和部门由通讯录决定，后端会在缺部门时按需补建。
        return provisionOaUser({
          oa_user_id: userForm.oa_user_id,
          username: userForm.username,
          password: userForm.password,
          role_ids: userForm.role_ids,
        })
      }
      const payload: Record<string, unknown> = {
        name: userForm.name,
        mobile: userForm.mobile || null,
        email: userForm.email || null,
        ...(!userEditTarget?.department_managed_by_oa && { department_id: userForm.department_id ?? null }),
        role_ids: userForm.role_ids,
      }
      if (userEditTarget) {
        // 用户管理不修改登录名；本人可在个人设置修改。密码留空不重置。
        if (userForm.password) payload.password = userForm.password
        return updateUser(userEditTarget.id, payload)
      }
      return createUser({
        ...payload,
        username: userForm.username,
        password: userForm.password,
      })
    },
    onSuccess: () => {
      Toast.success(userEditTarget ? '用户已保存' : userForm.oa_user_id ? '已按同序身份创建 CRM 账号' : '用户已创建')
      setUserModal(false)
      refreshUsers()
      void queryClient.invalidateQueries({ queryKey: ['settings-oa-members'] })
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const userStatusMutation = useMutation({
    mutationFn: ({ id, enable }: { id: number; enable: boolean }) =>
      enable ? enableUser(id) : disableUser(id),
    onSuccess: (_data, variables) => {
      Toast.success(variables.enable ? '账号已启用' : '账号已停用')
      refreshUsers()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const accountRemoval = useMutation({
    mutationFn: async ({ id, archive }: { id: number; archive: boolean }) => {
      if (archive) await archiveUser(id)
      else await deleteUnusedUser(id)
    },
    onSuccess: (_, { archive }) => {
      Toast.success(archive ? '账号已离职归档' : '空账号已删除')
      resetUserPage()
      refreshUsers()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  function confirmAccountRemoval(record: SystemUser, archive: boolean) {
    Modal.confirm({
      title: archive ? `离职归档「${record.name}」？` : `删除空账号「${record.name}」？`,
      content: archive
        ? '归档后该员工无法登录，历史记录会保留。请核对名下的客户和订单，未办事项可到「销售任务 → 待办交接」处理。'
        : '仅从未登录、没有业务记录且没有绑定外部身份的账号可以删除。删除后无法恢复。',
      okText: archive ? '离职归档' : '删除账号',
      onOk: () => accountRemoval.mutateAsync({ id: record.id, archive }),
    })
  }

  const dtSyncMutation = useMutation({
    mutationFn: syncDingTalkUsers,
    onSuccess: (result) => {
      setDtSyncResult(result)
      setDtSyncVisible(true)
      if (!result.disabled) {
        // 绑定了要立刻反映到列表上（刷新走"先取消再失效"，避免拿到在途的旧结果）
        refreshUsers()
      }
    },
    onError: () => {
      setDtSyncResult(null)
      setDtSyncVisible(true)
    },
  })
  const dtSyncErrorStatus = dtSyncMutation.error instanceof ApiError ? dtSyncMutation.error.status : undefined

  const [deptAlignResult, setDeptAlignResult] = useState<DepartmentAlignResult | null>(null)
  const deptAlignMutation = useMutation({
    mutationFn: alignDingTalkDepartments,
    onSuccess: (result) => {
      setDeptAlignResult(result)
      refreshUsers()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const roleSaveMutation = useMutation({
    mutationFn: () =>
      roleEditTarget
        ? updateRole(roleEditTarget.id, {
            name: roleForm.name,
            description: roleForm.description || null,
            data_scope: roleForm.data_scope,
            status: roleForm.status,
            permission_codes: roleForm.permission_codes,
          })
        : createRole({
            code: roleForm.code,
            status: roleForm.status,
            name: roleForm.name,
            description: roleForm.description || null,
            data_scope: roleForm.data_scope,
            permission_codes: roleForm.permission_codes,
          }),
    onSuccess: () => {
      Toast.success(roleEditTarget ? '角色已保存' : '角色已创建')
      setRoleModal(false)
      refreshRoles()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const deleteRoleMutation = useMutation({
    mutationFn: (id: number) => deleteRole(id),
    onSuccess: () => {
      Toast.success('角色已删除')
      refreshRoles()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const deptSaveMutation = useMutation({
    mutationFn: () =>
      deptEditTarget
        ? updateDepartment(deptEditTarget.id, {
            name: deptForm.name,
            parent_id: deptForm.parent_id ?? null,
            wecom_department_id: deptForm.wecom_department_id || null,
          })
        : createDepartment({
            name: deptForm.name,
            parent_id: deptForm.parent_id ?? null,
            wecom_department_id: deptForm.wecom_department_id || null,
          }),
    onSuccess: () => {
      Toast.success(deptEditTarget ? '部门已保存' : '部门已创建')
      setDeptModal(false)
      refreshDepts()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const deleteDeptMutation = useMutation({
    mutationFn: (id: number) => deleteDepartment(id),
    onSuccess: () => {
      Toast.success('部门已删除')
      refreshDepts()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const departments = deptsQuery.data ?? []
  const departmentsById = new Map(departments.map((dept) => [dept.id, dept]))
  const deptOptions = departments.map((dept) => {
    const path = [dept.name]
    const visited = new Set([dept.id])
    let parent = departmentsById.get(dept.parent_id ?? 0)
    while (parent && !visited.has(parent.id)) {
      path.unshift(parent.name)
      visited.add(parent.id)
      parent = departmentsById.get(parent.parent_id ?? 0)
    }
    return { value: dept.id, label: path.join(' / ') }
  }).sort((left, right) => left.label.localeCompare(right.label, 'zh-CN'))
  const roleOptions = (rolesQuery.data ?? []).map((role) => ({
    value: role.id,
    label: `${role.name}（${role.code}）`,
  }))
  // 角色 / 部门的本地筛选：接口一次返回全部，没必要为它去改后端。
  // 关键字同时匹配"看得见的名"和"机器用的码"—— 管理员手里拿到的可能是
  // `sales_manager` 这种码，也可能是「销售主管」这个名，只匹配一边会搜不到。
  const roleKey = roleKeyword.trim().toLowerCase()
  const filteredRoles = (rolesQuery.data ?? []).filter(
    (role) =>
      !roleKey ||
      role.name.toLowerCase().includes(roleKey) ||
      role.code.toLowerCase().includes(roleKey),
  )
  const deptKey = deptKeyword.trim().toLowerCase()
  const filteredDepts = (deptsQuery.data ?? []).filter(
    (dept) => !deptKey || dept.name.toLowerCase().includes(deptKey),
  )
  const scopeOptions = [
    { value: 'self', label: '仅本人' },
    { value: 'department', label: '本部门' },
    { value: 'department_and_sub', label: '本部门及下级' },
    { value: 'all', label: '全部' },
  ]

  // 角色标签按「身份」配色，不按顺序随机：同一个角色在任何屏幕上都是同一个色，
  // 管理员=蓝（最高权限）/ 销售主管=橙（管理层）/ 业务员=青（一线）/
  // 财务=紫（钱相关）/ 产品·开发评审=绿（技术）。没映射到的自定义角色给灰色。
  const ROLE_TONE: Record<string, 'blue' | 'orange' | 'cyan' | 'violet' | 'green'> = {
    admin: 'blue',
    sales_manager: 'orange',
    salesperson: 'cyan',
    finance: 'violet',
    product_reviewer: 'green',
  }

  const userColumns = [
    {
      // 登录名并到姓名下方一行小字：这两列本来就成对出现，
      // 拆成两列各占 140+150，是这张表横向滚动的最大一块。
      title: '姓名',
      dataIndex: 'name',
      width: 135,
      render: (name: string, record: SystemUser) => (
        <div>
          <div style={{ fontWeight: 500 }}>{name}</div>
          <div style={{ marginTop: 2, fontSize: 12, color: 'var(--crm-text-3)' }}>
            {record.username}
          </div>
        </div>
      ),
    },
    { title: '部门', dataIndex: 'department', width: 85, render: (v: string | null) => v ?? '-' },
    {
      title: '角色',
      dataIndex: 'roles',
      width: 130,
      render: (roles: SystemUser['roles']) =>
        (roles ?? []).length === 0 ? (
          <span style={{ color: 'var(--crm-text-3)' }}>-</span>
        ) : (
          <span style={{ display: 'inline-flex', flexWrap: 'wrap', gap: 4 }}>
            {(roles ?? []).map((role) => {
              // 停用的角色仍挂在人身上（不删关联），但已经**不生效**：
              // 权限与数据范围都按启用中的角色算。界面上要看得出来，否则
              // 「这个人明明有权限却进不去」会变成排查不了的谜题（第十批 10.10）。
              const disabled = Boolean(role.status) && role.status !== 'active'
              return (
                <StatusDot
                  key={role.id}
                  label={role.name + (disabled ? '（已停用）' : '')}
                  tone={(disabled ? 'grey' : ROLE_TONE[role.code] ?? 'grey') as TagTone}
                />
              )
            })}
          </span>
        ),
    },
    // 「手机」列也摘掉了（2026-11-19 主人：「手机邮箱是根本就不用填呀」）。
    // 这套系统是账号密码登录，不发短信、不找回密码，手机号对管理员没有读的价值。
    // 「同步账号」（旧「钉钉」）这一列摘掉了（2026-11-19）。
    // 主人问「同步账号是什么意思，同序 oa 那个吗」—— 它不是同序 OA 的模板编号，
    // 而是钉钉 / 企业微信里的 userid（决定这个人能不能扫码登录）。但这套登录
    // 没启用，列里 11 个人清一色"未绑定"，占 96px 却没有任何可操作信息；
    // 钉钉账号的匹配结果可在同步弹窗中查看。
    {
      title: '状态',
      dataIndex: 'status',
      width: 65,
      render: (v: string) => (
        <StatusDot label={v === 'archived' ? '离职归档' : v === 'active' ? '启用' : '停用'} tone={v === 'active' ? 'green' : 'grey'} />
      ),
    },
    {
      title: '操作',
      // 120 不是 100：两个无边框小按钮（编辑 / 停用）加上单元格内边距，
      // 100 时「停用」会被省略成「…」，等于把功能藏掉了
      width: isAdmin ? 270 : 120,
      // 表格比容器宽时固定在右侧：编辑/停用这两个按钮永远看得见
      fixed: 'right' as const,
      render: (_: unknown, record: SystemUser) => record.status === 'archived' ? '—' : (
        <>
          <Button
            theme="borderless"
            size="small"
            onClick={() => {
              setUserEditTarget(record)
              setUserForm({
                name: record.name,
                username: record.username,
                password: '',
                mobile: record.mobile ?? '',
                email: record.email ?? '',
                department_id: record.department_id ?? undefined,
                role_ids: (record.roles ?? []).map((role) => role.id),
              })
              setUserModal(true)
            }}
          >
            编辑
          </Button>
          {isAdmin && (
            <Button theme="borderless" size="small" onClick={() => {
              setOaBindingTarget(record)
              setSelectedOaMember(record.oa_user_id ?? null)
            }}>
              {record.oa_user_id ? '同序已绑定' : '绑定同序'}
            </Button>
          )}
          <Popconfirm
            title={`确认${record.status === 'active' ? '停用' : '启用'}账号「${record.name}」？`}
            content={record.status === 'active' ? '停用后该账号将无法继续使用系统。' : '启用后该账号可按现有权限使用系统。'}
            onConfirm={() => userStatusMutation.mutate({ id: record.id, enable: record.status !== 'active' })}
          >
            {/* 停用是危险动作，但一屏九行每行一个红字，等于每行都在喊「我是危险
                操作」，反而看不出哪一行真要留意。改成平时灰字、鼠标移上去才变红
                的悬停式危险色（规则见 index.css .crm-danger-hover）。 */}
            <Button
              theme="borderless"
              size="small"
              className={record.status === 'active' ? 'crm-danger-hover' : undefined}
              type={record.status === 'active' ? 'tertiary' : 'primary'}
            >
              {record.status === 'active' ? '停用' : '启用'}
            </Button>
          </Popconfirm>
          {isAdmin && record.status === 'disabled' && (
            <Button theme="borderless" size="small" type="danger"
              disabled={accountRemoval.isPending}
              onClick={() => confirmAccountRemoval(record, false)}>
              删除
            </Button>
          )}
          {isAdmin && (
            <Dropdown trigger="click" render={
              <Dropdown.Menu>
                <Dropdown.Item onClick={() => confirmAccountRemoval(record, true)}>离职归档</Dropdown.Item>
              </Dropdown.Menu>
            }>
              <Button size="small" theme="borderless" type="tertiary" disabled={accountRemoval.isPending}>更多</Button>
            </Dropdown>
          )}
        </>
      ),
    },
  ]

  const roleColumns = [
    {
      // 角色码并到角色名下面一行小字（同用户表把登录名并进姓名）：
      // 这两列成对出现、各占 150+130，是这张表横向滚动的最大一块。
      title: '角色',
      dataIndex: 'name',
      width: 145,
      render: (name: string, record: SystemRole) => (
        <div>
          <div style={{ fontWeight: 500 }}>{name}</div>
          <div style={{ marginTop: 2, fontSize: 12, color: 'var(--crm-text-3)' }}>
            {record.code}
          </div>
        </div>
      ),
    },
    {
      // 停用状态必须能一眼看到：停用的角色仍然挂在人身上，但已经不参与
      // 权限与数据范围计算（第十批 10.10）。
      title: '状态',
      dataIndex: 'status',
      width: 70,
      render: (v: string) => <StatusDot label={v === 'active' ? '启用' : '停用'} tone={v === 'active' ? 'green' : 'grey'} />,
    },
    {
      title: '数据范围',
      dataIndex: 'data_scope',
      width: 105,
      render: (v: string) =>
        ({ self: '仅本人', department: '本部门', department_and_sub: '本部门及下级', all: '全部' })[
          v
        ] ?? v,
    },
    {
      title: '权限数',
      dataIndex: 'permission_codes',
      width: 70,
      render: (codes: string[] | undefined) => (codes ?? []).length,
    },
    // 说明给固定宽而不是自适应：不写宽度时它会被挤到几十像素、每行折成三行
    { title: '说明', dataIndex: 'description', width: 170, render: (v: string | null) => v ?? '-' },
    {
      title: '操作',
      // 120：编辑 + 删除两个无边框小按钮，100 时「删除」会被省略成「…」
      width: 120,
      render: (_: unknown, record: SystemRole) => (
        <>
          <Button
            theme="borderless"
            size="small"
            onClick={() => {
              setRoleEditTarget(record)
              setRoleForm({
                code: record.code,
                name: record.name,
                description: record.description ?? '',
                data_scope: record.data_scope,
                status: record.status ?? 'active',
                permission_codes: record.permission_codes ?? [],
              })
              setRoleModal(true)
            }}
          >
            编辑
          </Button>
          <Popconfirm
            title={`删除角色「${record.name}」？`}
            onConfirm={() => deleteRoleMutation.mutate(record.id)}
          >
            <Button theme="borderless" type="tertiary" size="small" className="crm-danger-hover">
              删除
            </Button>
          </Popconfirm>
        </>
      ),
    },
  ]

  const deptColumns = [
    // 给固定宽：不写宽度时这一列会吃掉全部剩余空间（实测 435px 只放「销售部」三个字），
    // 右边几列被挤成一条
    { title: '部门名称', dataIndex: 'name', width: 300 },
    {
      title: '上级部门',
      dataIndex: 'parent_id',
      width: 140,
      render: (v: number | null) =>
        v ? (deptOptions.find((d) => d.value === v)?.label ?? `部门编号 ${v}`) : '顶级',
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      // 和角色表统一用 Tag：同一页里两张表的状态长得不一样，用户会以为是两种东西
      render: (v: string) => <StatusDot label={v === 'active' ? '启用' : '停用'} tone={v === 'active' ? 'green' : 'grey'} />,
    },
    {
      title: '操作',
      width: 140,
      render: (_: unknown, record: SystemDepartment) => (
        <>
          <Button
            theme="borderless"
            size="small"
            onClick={() => {
              setDeptEditTarget(record)
              setDeptForm({
                name: record.name,
                parent_id: record.parent_id ?? undefined,
                wecom_department_id: record.wecom_department_id ?? '',
              })
              setDeptModal(true)
            }}
          >
            编辑
          </Button>
          <Popconfirm
            title={`删除部门「${record.name}」？下级部门或用户非空时会拒绝。`}
            onConfirm={() => deleteDeptMutation.mutate(record.id)}
          >
            <Button theme="borderless" type="tertiary" size="small" className="crm-danger-hover">
              删除
            </Button>
          </Popconfirm>
        </>
      ),
    },
  ]

  return (
    <div className="page-container settings-page">
      {notePrompt.node}
      <PageHeader
        // 标题跟着**当前页签**走，不能一律写「系统设置」（返修单 R12 的口径）：
        // 主管只有公海回收复核权，他翻到那一页时标题必须写「公海回收复核」，
        // 否则他会以为自己拿到了整个系统设置、跑去点用户和角色。
        // 翻到「我的」时同理 —— 那是他自己的东西，不叫「系统设置」。
        title={hasManagementTabs ? '系统设置' : '我的'}
        subtitle={hasManagementTabs ? undefined : '管理登录名、密码，查看资料与权限'}
        extra={hasManagementTabs && (
          <Input className="settings-search-input" aria-label="查找设置" placeholder="查找设置，如付款条件、查重"
            showClear value={settingSearch} onChange={setSettingSearch} />
        )}
      />
      <div className={`settings-shell${hasManagementTabs ? '' : ' is-personal'}`}>
          {hasManagementTabs && searchTerms.length > 0 && (
            <div className="settings-search-results">
              {settingMatches.length > 0 ? <ul aria-label="设置搜索结果">
                {settingMatches.map((entry) => (
                  <li key={`${entry.to}-${entry.label}`}>
                    <Link to={entry.to} onClick={() => setSettingSearch('')}>
                      <strong>{entry.label}</strong>
                      <span>{entry.place}</span>
                    </Link>
                    {entry.description && <p>{entry.description}</p>}
                  </li>
                ))}
              </ul> : <p role="status">没有找到相关设置。可以试试「权限」「通知」或「付款条件」。</p>}
            </div>
          )}
          {hasManagementTabs && (
            <label className="settings-nav-compact">
              <span>设置分类</span>
              <select value={activeKey} onChange={(event) => setSearchParams({ tab: event.target.value })}>
                {tabGroups.map((group) => (
                  <optgroup key={group.id} label={group.title}>
                    {group.items.map((item) => <option key={item.itemKey} value={item.itemKey}>{item.tab}</option>)}
                  </optgroup>
                ))}
              </select>
            </label>
          )}
          <nav className="settings-nav" aria-label="设置分类">
            {tabGroups.map((group) => (
              <div className="settings-nav-group" key={group.id} data-group={group.id}>
                <div className="settings-nav-title">{group.title}</div>
                {group.items.map((item) => (
                  <Link
                    key={item.itemKey}
                    className={`settings-nav-item${activeKey === item.itemKey ? ' active' : ''}`}
                    aria-current={activeKey === item.itemKey ? 'page' : undefined}
                    to={`/settings?tab=${item.itemKey}`}
                  >
                    <item.icon aria-hidden="true" />
                    <span>{item.tab}</span>
                  </Link>
                ))}
              </div>
            ))}
          </nav>
          <section className="settings-content" aria-labelledby="settings-pane-title">
          <header className="settings-pane-header">
            <h3 id="settings-pane-title" className="settings-content-heading">{hasManagementTabs ? currentTab.tab : '个人账号'}</h3>
            {hasManagementTabs && <p>{currentTab.description}</p>}
          </header>
          {activeKey === 'profile' && <PersonalProfilePanel />}
          {activeKey === 'users' && canManageUsers && (
            <>
              <div className="toolbar settings-toolbar">
                {/* 能搜什么要写在占位符上：不写清楚，等于没做。
                    后端按「姓名或登录名」模糊查（`User.name.ilike | User.username.ilike`）。 */}
                <Input
                  placeholder="搜索姓名 / 登录名"
                  value={userKeywordInput}
                  onChange={setUserKeywordInput}
                  onEnterPress={() => {
                    setUserKeyword(userKeywordInput.trim())
                    resetUserPage()
                  }}
                  style={{ width: 220 }}
                  showClear
                />
                <Button
                  onClick={() => {
                    setUserKeyword(userKeywordInput.trim())
                    resetUserPage()
                  }}
                >
                  查询
                </Button>
                <Select
                  placeholder="全部状态"
                  value={userStatus || undefined}
                  onChange={(value) => {
                    setUserStatus((value as string) ?? '')
                    resetUserPage()
                  }}
                  style={{ width: 120 }}
                  showClear
                  optionList={[
                    { value: 'active', label: '启用' },
                    { value: 'disabled', label: '停用' },
                    { value: 'archived', label: '离职归档' },
                  ]}
                />
                <Select
                  placeholder="全部部门"
                  value={userDeptId}
                  onChange={(value) => {
                    setUserDeptId(value as number | undefined)
                    resetUserPage()
                  }}
                  style={{ width: 160 }}
                  showClear
                  filter
                  optionList={deptOptions}
                />
                <div style={{ flex: 1 }} />
                <Popconfirm title="确认同步钉钉账号？" content="系统会从钉钉通讯录匹配已有的 CRM 账号，用于钉钉扫码登录。" onConfirm={() => dtSyncMutation.mutate()}>
                  <Button loading={dtSyncMutation.isPending}>同步钉钉账号</Button>
                </Popconfirm>
                <Popconfirm
                  title="按同序部门覆盖现有账号归属？"
                  content="只改部门，不改角色。数据范围是「本部门」的账号，可见数据会跟着部门变化，请核对结果。"
                  onConfirm={() => deptAlignMutation.mutate()}
                >
                  <Button loading={deptAlignMutation.isPending}>按同序对齐部门</Button>
                </Popconfirm>
                <Button type="primary"
                  theme="outline"
                  onClick={() => {
                    setUserEditTarget(null)
                    setUserForm({
                      name: '',
                      username: '',
                      password: '',
                      mobile: '',
                      email: '',
                      role_ids: [],
                      oa_user_id: undefined,
                      oa_department: undefined,
                    })
                    setUserModal(true)
                  }}
                >
                  新建用户
                </Button>
              </div>
              <Table<SystemUser> className="table-center settings-list-table"
                // 等于各列宽之和（730）。设置页左侧还有一条二级导航，
                // 表格区只有约 660 —— 写够总宽再配 fixed 操作列，比把列压扁可读
                scroll={{ x: isAdmin ? 840 : 730 }}
                columns={userColumns}
                dataSource={usersQuery.isError ? [] : usersQuery.data?.items ?? []}
                loading={usersQuery.isLoading}
                rowKey="id"
                empty={emptyTextNode(usersQuery, userKeyword || userStatus || userDeptId
                  ? '没有符合条件的用户，试试放宽筛选条件' : '还没有用户')}
                pagination={{
                  currentPage: userPage,
                  pageSize: userPageSize,
                  total: usersQuery.data?.total ?? 0,
                  onPageChange: setUserPage,
                  showSizeChanger: true,
                  pageSizeOpts: [10, 20, 50, 100],
                  onPageSizeChange: (size: number) => { setUserPageSize(size); setUserPage(1) },
                }}
              />
            </>
          )}
          {activeKey === 'roles' && (
            <>
              <div className="toolbar settings-toolbar">
                {/* 本地筛就够：角色接口一次返回全部，通常也就几个到十几个。 */}
                <Input
                  placeholder="搜索角色码 / 角色名"
                  value={roleKeyword}
                  onChange={setRoleKeyword}
                  style={{ width: 220 }}
                  showClear
                />
                <div style={{ flex: 1 }} />
                <Button type="primary"
                  theme="outline"
                  onClick={() => {
                    setRoleEditTarget(null)
                    setRoleForm({
                      code: '',
                      name: '',
                      description: '',
                      data_scope: 'self',
                      status: 'active',
                      permission_codes: [],
                    })
                    setRoleModal(true)
                  }}
                >
                  新建角色
                </Button>
              </div>
              <Table<SystemRole> className="table-center settings-list-table"
                columns={roleColumns}
                dataSource={rolesQuery.isError ? [] : filteredRoles}
                loading={rolesQuery.isLoading}
                rowKey="id"
                empty={emptyTextNode(rolesQuery, roleKeyword ? '没有匹配的角色' : '还没有角色')}
                pagination={false}
              />
            </>
          )}
          {activeKey === 'departments' && (
            <>
              <div className="toolbar settings-toolbar">
                {/* 同上：部门接口一次返回全部，本地筛即可。 */}
                <Input
                  placeholder="搜索部门名称"
                  value={deptKeyword}
                  onChange={setDeptKeyword}
                  style={{ width: 220 }}
                  showClear
                />
                <div style={{ flex: 1 }} />
                <Button type="primary"
                  theme="outline"
                  onClick={() => {
                    setDeptEditTarget(null)
                    setDeptForm({ name: '', wecom_department_id: '' })
                    setDeptModal(true)
                  }}
                >
                  新建部门
                </Button>
              </div>
              <Table<SystemDepartment> className="table-center settings-list-table"
                columns={deptColumns}
                dataSource={deptsQuery.isError ? [] : filteredDepts}
                loading={deptsQuery.isLoading}
                rowKey="id"
                empty={emptyTextNode(deptsQuery, deptKeyword ? '没有匹配的部门' : '还没有部门')}
                pagination={false}
              />
            </>
          )}

          {activeKey === 'tags' && (
            <>
              <form
                className="toolbar settings-toolbar"
                onSubmit={(event) => {
                  event.preventDefault()
                  if (createTagMutation.isPending) return
                  const name = tagForm.name.trim()
                  if (!name) {
                    setTagCreateError('请填写标签名称')
                    tagNameInput.current?.focus()
                    return
                  }
                  setTagCreateError('')
                  createTagMutation.mutate({ ...tagForm, name })
                }}
              >
                <FormLabel required style={{ marginBottom: 0 }}>
                  <label htmlFor="settings-tag-name">标签名称</label>
                </FormLabel>
                <Input
                  id="settings-tag-name"
                  aria-label="标签名称"
                  aria-required
                  aria-invalid={Boolean(tagCreateError)}
                  aria-describedby={tagCreateError ? 'settings-tag-error' : undefined}
                  validateStatus={tagCreateError ? 'error' : 'default'}
                  ref={tagNameInput}
                  style={{ width: 200 }}
                  placeholder="例如：重点跟进"
                  value={tagForm.name}
                  disabled={createTagMutation.isPending}
                  onChange={(value) => {
                    setTagForm({ ...tagForm, name: value })
                    setTagCreateError('')
                  }}
                />
                <Select
                  style={{ width: 150 }}
                  value={tagForm.type}
                  disabled={createTagMutation.isPending}
                  onChange={(value) => setTagForm({ ...tagForm, type: value as string })}
                  optionList={[
                    { value: 'custom', label: '自定义' },
                    { value: 'region', label: '区域' },
                    { value: 'industry', label: '行业' },
                    { value: 'grade', label: '等级' },
                    { value: 'channel', label: '渠道' },
                  ]}
                />
                <Button type="primary"
                  htmlType="submit"
                  theme="outline"
                  loading={createTagMutation.isPending}
                >
                  新建标签
                </Button>
                <div style={{ flex: 1 }} />
                <span style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
                  创建后可在客户详情里选择这个标签
                </span>
                {tagCreateError && (
                  <div id="settings-tag-error" role="alert" style={{ flexBasis: '100%', color: 'var(--crm-danger)', fontSize: 12 }}>
                    {tagCreateError}
                  </div>
                )}
              </form>
              <Table<TagRow> className="table-center settings-list-table"
                columns={[
                  { title: '标签名', dataIndex: 'name' },
                  {
                    // 后端存的是 type 原值（custom），直接渲染会把英文印给用户
                    title: '分组',
                    dataIndex: 'type',
                    width: 120,
                    render: (value: string) => TAG_GROUP_LABELS[value] ?? value,
                  },
                  {
                    title: '状态',
                    dataIndex: 'status',
                    width: 100,
                    render: (value: string) => (
                      <Tag size="small" color={value === 'active' ? 'green' : 'grey'}>
                        {value === 'active' ? '启用' : '停用'}
                      </Tag>
                    ),
                  },
                  {
                    title: '操作',
                    width: 150,
                    render: (_: unknown, record: TagRow) => (
                      <>
                        <Button
                          theme="borderless"
                          size="small"
                          onClick={() => setTagEditTarget(record)}
                        >
                          编辑
                        </Button>
                        <Popconfirm
                          title="删除标签会同时移除所有客户上的这个标签，确认？"
                          onConfirm={() => deleteTagMutation.mutate(record.id)}
                        >
                          <Button theme="borderless" type="tertiary" size="small" className="crm-danger-hover">
                            删除
                          </Button>
                        </Popconfirm>
                      </>
                    ),
                  },
                ]}
                dataSource={tagsQuery.isError ? [] : tagsQuery.data ?? []}
                loading={tagsQuery.isLoading}
                rowKey="id"
                pagination={false}
                empty={emptyTextNode(tagsQuery, '还没有标签', '创建后可在客户详情选择。')}
              />
            </>
          )}
          {activeKey === 'dictionaries' && (
            <>
              {/* 原来顶着一整段说明书，还夹了一句"产品分类在别处维护" —— 那是别的页的事，
                  读者在这儿只需要知道"我停用一个选项会发生什么"。只留这一句。 */}
              <div style={{ color: 'var(--crm-text-3)', fontSize: 12, marginBottom: 10 }}>
                业务表单只能选择启用的选项；停用只是不再可选，历史数据不会被改写。
              </div>
              {/* 类型原来是个下拉，可点开**只有「客户等级」一项**（库里就这一种类型），
                  等于一个只能选自己的下拉。改成一句静态标题，顺带报个数。 */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                <span style={{ fontSize: 13, fontWeight: 600 }}>客户等级</span>
                <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                  共 {(dictionariesQuery.data ?? []).length} 项
                </span>
              </div>
              <div className="toolbar settings-toolbar">
                {/* 这几个框以前全靠 placeholder 自我介绍：鼠标一进去字就消失，
                    只剩三个空格子；「排序」那个框尤其像天书（0 代表什么？）。
                    给每个框配一个固定的小标签，输入时也看得见自己在填什么。 */}
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>编码</span>
                  <Input
                    style={{ width: 130 }}
                    placeholder="如 A"
                    value={dictionaryForm.code}
                    onChange={(value) => setDictionaryForm({ ...dictionaryForm, code: value })}
                  />
                </span>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>名称</span>
                  <Input
                    style={{ width: 170 }}
                    placeholder="如 甲级客户"
                    value={dictionaryForm.label}
                    onChange={(value) => setDictionaryForm({ ...dictionaryForm, label: value })}
                  />
                </span>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>排序</span>
                  <Input
                    style={{ width: 90 }}
                    placeholder="越小越前"
                    value={dictionaryForm.sort_no}
                    onChange={(value) => setDictionaryForm({ ...dictionaryForm, sort_no: value })}
                  />
                </span>
                <Button type="primary"
                  theme="outline"
                  loading={dictionaryMutation.isPending}
                  disabled={!dictionaryForm.code.trim() || !dictionaryForm.label.trim()}
                  onClick={() => dictionaryMutation.mutate()}
                >
                  新建字典项
                </Button>
                {/* 按钮置灰却不说为什么，用户只能猜（2026-11-17 视觉审视 V-04）。
                    原因常驻在按钮旁边，填全了自动消失。 */}
                {(!dictionaryForm.code.trim() || !dictionaryForm.label.trim()) && (
                  <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                    请先填写编码和名称
                  </span>
                )}
              </div>
              <Table<DictionaryItemRow> className="table-center settings-list-table"
                columns={[
                  // 「类型」这一列删了：库里只有 customer_level 一种，四行全是同一个词，
                  // 140px 换不来任何信息 —— 类型已经在表格上方写死了。
                  { title: '编码', dataIndex: 'code', width: 90 },
                  { title: '名称', dataIndex: 'label' },
                  { title: '排序', dataIndex: 'sort_no', width: 70 },
                  {
                    title: '状态', dataIndex: 'enabled', width: 90,
                    render: (value: boolean) => <Tag color={value ? 'green' : 'grey'}>{value ? '启用' : '停用'}</Tag>,
                  },
                  {
                    title: '操作', width: 120,
                    render: (_: unknown, record: DictionaryItemRow) => (
                      <span style={{ display: 'inline-flex', gap: 6 }}>
                        <Button
                          theme="borderless"
                          size="small"
                          loading={dictionaryUpdateMutation.isPending}
                          onClick={async () => {
                            const label = await notePrompt.ask({
                              title: '修改显示名称',
                              label: '显示名称',
                              description: `改的是业务表单里看到的名字（当前：${record.label}），不影响编码。`,
                              defaultValue: record.label,
                              required: true,
                              maxLength: 40,
                              okText: '保存',
                            })
                            if (label) dictionaryUpdateMutation.mutate({ id: record.id, changes: { label } })
                          }}
                        >
                          编辑
                        </Button>
                        <Button
                          // 停用会让业务表单里选不到这一项，是"有后果"的动作；
                          // 和左边的「编辑」一样染成蓝色，等于把危险动作藏进普通操作里。
                          theme="borderless"
                          type={record.enabled ? 'danger' : 'primary'}
                          size="small"
                          loading={dictionaryUpdateMutation.isPending}
                          onClick={() => {
                            const enabled = !record.enabled
                            Modal.confirm({
                              title: `${enabled ? '启用' : '停用'}字典项？`,
                              content: `${enabled ? '启用后' : '停用后'}，后续业务表单${enabled ? '可以' : '不再'}选择「${record.label}」；历史数据不会被改写。`,
                              onOk: () => dictionaryUpdateMutation.mutate({ id: record.id, changes: { enabled } }),
                            })
                          }}
                        >
                          {record.enabled ? '停用' : '启用'}
                        </Button>
                      </span>
                    ),
                  },
                ]}
                dataSource={dictionariesQuery.isError ? [] : dictionariesQuery.data ?? []}
                loading={dictionariesQuery.isLoading}
                rowKey="id"
                pagination={false}
                empty={emptyTextNode(dictionariesQuery, '还没有数据字典项')}
              />
            </>
          )}
          {activeKey === 'notifications' && <NotificationSettingsPanel />}
          {activeKey === 'audit' && (
            /* 审计日志：搜索 / 筛选 / 分页 + 变更明细（2026-10-15）。
               原来这里是一个写死 `{page:1, page_size:50}`、`pagination={false}` 的表格 ——
               **只能看最近 50 条、没有搜索、没有筛选**，所以主人说"太乱了"。
               后端一直支持那六个筛选参数，是页面没接。 */
            <AuditLogPanel active={activeKey === 'audit'} />
          )}
          {activeKey === 'rules' && canManageSettings && (
            // 必须写 gridTemplateColumns: 'minmax(0, 1fr)'：单列 grid 的默认列宽是 auto，
            // 会被子项的最小宽度撑开 —— 实测被撑到 1320（容器只有 923），
            // 于是整页出现横向滚动条、右边第三列输入框被裁到框外。
            <>
            {canManageSettings && (
              <div className="rule-tabs" role="tablist">
                {RULE_PANELS.map((panel) => (
                  <button
                    key={panel.key}
                    type="button"
                    role="tab"
                    aria-selected={panelKey === panel.key}
                    className={`rule-tab${panelKey === panel.key ? ' is-on' : ''}`}
                    onClick={() => setSearchParams({ tab: 'rules', section: panel.key })}
                  >
                    {panel.label}
                  </button>
                ))}
              </div>
            )}
            <div style={{ display: 'grid', gap: 16, gridTemplateColumns: 'minmax(0, 1fr)' }}>
              {settingsQuery.isError && (
                <p className="settings-help" role="status">
                  无法读取业务设置。 <Button theme="borderless" onClick={() => void settingsQuery.refetch()}>重新读取</Button>
                </p>
              )}
              {canManageSettings && (
                <div className={panelKey === 'unrated' ? undefined : 'panel-hidden'}>
                  <UnratedRulesPanel />
                </div>
              )}
              {/* ⚠️ 下面这三块（公海回收规则 / 自动任务规则 / 系统配置）都是**管理员**的活儿：
                  改规则、调参数、跑扫描，后端一律要 `settings:manage`。
                  主管只有 `customer:pool_review`，看到这些控件也点不动（会吃 403），
                  所以对非管理员**整块不渲染** —— 返修单 R12 要求"主管只进入本团队
                  复核功能"，露着不能用的按钮比藏起来更糟。 */}
              {canManageSettings && (
              <div className={`rule-block${panelKey === 'pool' ? '' : ' panel-hidden'}`}>
                <div className="rule-block-title">公海回收规则</div>
                {/* 说明收成两行 + 「展开」：这四句话合起来二十多行灰字，把下面那张表
                    压到屏幕外去了。默认两行够看明白"这规则干嘛的"，要抠细节再展开
                    （2026-11-17）。 */}
                <Typography.Paragraph
                  ellipsis={{ rows: 2, expandable: true }}
                  style={{ color: 'var(--crm-text-2)', fontSize: 12, lineHeight: 1.65, marginBottom: 12 }}
                >
                  客户超过设定天数没有跟进记录，就会被提名成回收候选。
                  回收不会自动发生 —— 要先预告、再由主管逐条或批量批准；
                  批准时还会再检查一遍这期间有没有新的跟进、报价、订单或回款。
                  客户手上还有在途订单、未结应收、有效正式报价或在途打样时会被保护。
                </Typography.Paragraph>
                <Table<PublicPoolRuleRow> className="table-center pool-rule-table"
                  columns={[
                    { title: '客户等级', dataIndex: 'level', render: (v: string) => customerLevelLabel(v) },
                    {
                      title: '未跟进天数',
                      dataIndex: 'days',
                      render: (days: number, record: PublicPoolRuleRow) => (
                        <Input
                          aria-label={`${customerLevelLabel(record.level)}未跟进天数`}
                          defaultValue={String(days)}
                          /* 单位补上（2026-11-19）：原来只有 30 / 60 / 90 三个数字，
                             表头写「未跟进天数」还算能猜到，但和自动任务表并排放时
                             一边有天一边没天，看着就是两套标准。 */
                          suffix="天"
                          style={{ width: 104, maxWidth: '100%' }}
                          onBlur={(event) => {
                            const raw = (event.target as HTMLInputElement).value.trim()
                            const next = Number(raw)
                            if (!raw || !Number.isInteger(next) || next <= 0) {
                              Toast.warning('公海回收天数须为正整数')
                              return
                            }
                            if (Number.isFinite(next) && next !== days) {
                              poolRuleMutation.mutate({ id: record.id, days: next })
                            }
                          }}
                        />
                      ),
                    },
                    {
                      title: '启用',
                      dataIndex: 'enabled',
                      render: (enabled: boolean, record: PublicPoolRuleRow) => (
                        <Switch
                          aria-label={`启用${customerLevelLabel(record.level)}回收规则`}
                          checked={enabled}
                          onChange={(value) =>
                            poolRuleMutation.mutate({ id: record.id, enabled: value })
                          }
                        />
                      ),
                    },
                    // 「说明」这一列写的是「A 级客户 30 天未跟进回收」，和前三列完全重复。
                    // 1024 下它只剩 30px，把表撑出横向滚动、右侧还被裁，所以整列去掉
                    // （2026-11-18）。
                  ]}
                  dataSource={(poolRulesQuery.isError ? [] : poolRulesQuery.data ?? []).filter((row) => row.level !== 'UNRATED')}
                  loading={poolRulesQuery.isFetching}
                  empty={emptyTextNode(poolRulesQuery, '还没有公海回收规则')}
                  rowKey="id"
                  pagination={false}
                />
                <div className="pool-scan-toolbar">
                  <Popconfirm title="确认扫描并生成回收预告？" content="会按当前规则生成待复核候选，不会直接回收客户。" onConfirm={() => recycleMutation.mutate()}>
                    <Button loading={recycleMutation.isPending}>扫描并生成回收预告</Button>
                  </Popconfirm>
                  <span>
                    定时任务也会按这些规则生成回收预告，由主管复核。
                  </span>
                </div>
                {poolScan && (
                  <section className="pool-scan-result" aria-label="本次公海扫描结果">
                    <div className="pool-scan-result-heading">
                      <strong role="status">{poolScan.nominated_count
                        ? `新生成 ${poolScan.nominated_count} 条回收预告`
                        : '本次没有新增回收预告'}</strong>
                      <Link to="/pool-recycle">打开公海回收复核</Link>
                    </div>
                    {poolScan.enabled_rule_count === 0 && <div>当前没有启用的回收规则。</div>}
                    {Boolean(poolScan.protection_overridden_count) && (
                      <div>其中 {poolScan.protection_overridden_count} 位客户仍有履约事项，请复核时重点核对。</div>
                    )}
                    {poolScanReasons.length > 0 && <ul>
                      {poolScanReasons.map(({ label, count }) => <li key={label}>{label} · {count} 位</li>)}
                    </ul>}
                  </section>
                )}
              </div>
              )}

              {canManageSettings && (
                <div className={`rule-block${panelKey === 'pool' ? '' : ' panel-hidden'}`}>
                  <div className="rule-block-title">回收等待时间</div>
                  {/* 原来用 Typography ellipsis 折叠两行 —— 这段里带 <b>，
                      折叠模式把元素串成 [object Object] 显示出来（1024 下亲眼看到）。
                      说明本身不长，直接整段铺开，不再折叠。 */}
                  <div
                    style={{ color: 'var(--crm-text-2)', fontSize: 12, lineHeight: 1.65, marginBottom: 12 }}
                  >
                    保存预告期或暂缓期后，待复核、已暂缓的候选会按对应的新天数重算到期时间。
                    修改与重算记录可在审计日志查看。
                  </div>
                  {/* 三项原来横排成三列，每列下面挂一段三四行的灰色说明 ——
                      三段说明离得远、又都压在下沿，读起来像三张便签贴在一起。
                      改成一行一项：名称 200px、控件 200 上下、说明右对齐占剩下的宽度，
                      每项一行或两行，纵向是一条干净的左边缘。 */}
                  <div style={{ display: 'grid', gap: 18 }}>
                    <div className="recycle-pace-row">
                      <FormLabel>回收预告期（天）</FormLabel>
                      <SettingValueField
                        rowKey="pool_recycle_notice_days"
                        field="days"
                        value={noticeSetting?.value?.days}
                        onSave={(next) => settingMutation.mutateAsync({ key: 'pool_recycle_notice_days', value: next, silent: true })}
                      />
                      <div style={{ color: 'var(--crm-text-3)', fontSize: 12, marginTop: 4 }}>
                        扫描命中后先预告这么多天，期间业务员仍可跟进自救；
                        没满之前主管只能走「提前回收」例外。0 = 不设缓冲。
                      </div>
                    </div>
                    <div className="recycle-pace-row">
                      <FormLabel>主管暂缓等待期（天）</FormLabel>
                      <SettingValueField
                        rowKey="pool_recycle_defer_days"
                        field="days"
                        value={deferSetting?.value?.days}
                        onSave={(next) => settingMutation.mutateAsync({ key: 'pool_recycle_defer_days', value: next, silent: true })}
                      />
                      <div style={{ color: 'var(--crm-text-3)', fontSize: 12, marginTop: 4 }}>
                        主管点「暂缓」之后要等这么多天才能再批准回收
                        （暂缓期内随时可以驳回结案）。
                      </div>
                    </div>
                    <div className="recycle-pace-row">
                      <FormLabel>{settingLabel('pool_recycle_max_protect_days')}</FormLabel>
                      <SettingValueField
                        rowKey="pool_recycle_max_protect_days"
                        field="days"
                        value={protectSetting?.value?.days}
                        onSave={(next) => settingMutation.mutateAsync({ key: 'pool_recycle_max_protect_days', value: next, silent: true })}
                      />
                      <div className="settings-help">{settingDescription('pool_recycle_max_protect_days')}</div>
                    </div>
                  </div>
                </div>
              )}

              {/* 复核已经搬成独立页（/pool-recycle）。这里只留一句指路：
                  规则页改的是"多少天算冷落、保护多久"，批不批是另一件事。 */}
              <div className={`rule-block-hint${panelKey === 'pool' ? '' : ' panel-hidden'}`}>
                <span>候选的复核在「客户中心 → 回收复核」页签里处理，这里只改规则。</span>
                <Link to="/customers">去客户中心 →</Link>
              </div>

              {canManageSettings && panelKey === 'auto' && (
                <AutoTaskRulesPanel
                  rules={(taskRulesQuery.data ?? []).filter((row) => row.code !== 'customer_unrated_silent')}
                  pending={taskRulesQuery.isLoading}
                  failed={taskRulesQuery.isError}
                  saving={taskRuleMutation.isPending || taskRuleStatusMutation.isPending}
                  scanning={autoTaskMutation.isPending}
                  onReload={() => void taskRulesQuery.refetch()}
                  onSave={(id, days) => taskRuleMutation.mutateAsync({ id, days })}
                  onToggle={(id, active) => taskRuleStatusMutation.mutateAsync({ id, active })}
                  onScan={() => autoTaskMutation.mutate()}
                />
              )}

              {canManageSettings && (
              // is-plain：这一块不再套白卡 —— 六个分组本身就是卡片，
              // 卡中卡（白底套白底、只靠一条描边区分）正是"廉价感"的来源。
              /* 总标题去掉：一次只显示一个组，组自己就有组头，
                 上面再压一个「系统配置」等于同一句话说两遍。 */
              <div className={`rule-block is-plain${panelKey.startsWith('g-') ? '' : ' panel-hidden'}`}>
                {/* 分组卡片（2026-11-19）：29 项铺一张表 → 按业务域分六组、
                    每组一张卡，组内是「名称 | 控件 | 一句话说明」的表单行，
                    控件宽度封顶 240，不再横跨整行。 */}
                <div className="settings-groups">
                  {SETTING_GROUPS.map((group) => {
                    const rows = group.keys
                      .map((key) => settingRow(key))
                      .filter((row): row is SystemSettingRow => Boolean(row))
                    if (!rows.length) return null
                    return (
                      <section
                        className={`settings-group${panelKey === `g-${group.id}` ? '' : ' panel-hidden'}`}
                        key={group.title}
                        style={{ '--group-color': group.color } as CSSProperties}
                      >
                        <header className="settings-group-head">
                          <span className="settings-group-title">{group.title}</span>
                          {group.note && <span className="settings-group-note">{group.note}</span>}
                        </header>
                        <div className="settings-group-body">
                          {rows.map((row) => {
                            // 传上当前值：同序 OA 那三条"待配置"是写死的文案，
                            // 实际配好了还挂着，等于给对的东西贴错标签。
                            const note = settingNote(row.key)
                            // 多行文本（交货条款）独占一整行：塞在 240 宽的列里会被挤成
                            // 三行字、把这一行撑到 180px 高，上下全是空白。
                            const kind = SETTING_FIELD_META[row.key]?.kind
                            return (
                              <div key={row.key}>
                              <div className={`setting-field${kind === 'multiline' ? ' is-multiline' : ''}`} key={row.key}>
                                <span className="setting-field-label">{row.key === 'dedup_scoring' ? '客户查重阈值' : settingLabel(row.key)}</span>
                                <div className="setting-field-value">{renderSettingValue(row)}</div>
                                <div className="setting-field-desc">
                                  <span>{settingDescription(row.key, row.description)}</span>
                                  {note && (
                                    <span className="setting-field-tag">
                                      <Tag color="orange" size="small">{note.tag}</Tag>
                                      {note.text}
                                    </span>
                                  )}
                                </div>
                              </div>
                              {row.key === 'dedup_scoring' && <CustomerScoreDetails details={row.customer_score_details} />}
                              </div>
                            )
                          })}
                        </div>
                        <div className="settings-group-foot">
                          {group.hint}
                          {group.id === 'company' && <>{' '}<Link to="/documents">去文档管理</Link></>}
                          {group.id === 'customer' && <>{' '}<Link to="?tab=rules&section=auto">前往自动任务</Link></>}
                        </div>
                      </section>
                    )
                  })}
                </div>

                {/* 报价审批分级：从上面那张表里拿出来的独立区块。
                    它本来就是"一张会改的子表格"，跟上面那些"一行一个键值"的
                    配置项不是同一类东西，硬塞进一格只会把行高顶爆。
                    2026-11-19 挂在「报价与定价」页签下（原来那个「审批与外部」页签
                    只装同序 OA 三个模板号，整页已经删了，这块得跟过去——
                    它本来就是"按报价金额定谁能批"，放报价页更贴题）。 */}
                {(() => {
                  const approvalRow = (settingsQuery.data ?? []).find((row) => row.key === 'approval_levels')
                  const levels = approvalRow?.value?.levels
                  if (!approvalRow || !Array.isArray(levels)) return null
                  return (
                    <div
                      className={panelKey === 'g-pricing' ? undefined : 'panel-hidden'}
                      style={{
                        marginTop: 24,
                        paddingTop: 20,
                        borderTop: '1px solid var(--crm-border)',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 10 }}>
                        <span style={{ fontSize: 14, fontWeight: 600 }}>报价审批分级</span>
                        <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                          按报价金额决定审批级别和可审批角色；表格顺序就是判定顺序
                        </span>
                      </div>
                      <ApprovalLevelsEditor
                        key={JSON.stringify(approvalRow.value)}
                        levels={levels as Array<Record<string, unknown>>}
                        saving={settingMutation.isPending}
                        onSave={(next) =>
                          settingMutation.mutate({ key: approvalRow.key, value: { levels: next } })
                        }
                      />
                    </div>
                  )
                })()}
              </div>
              )}
            </div>
            </>
          )}
          </section>
      </div>

      <Modal
        title="钉钉账号同步"
        visible={dtSyncVisible}
        onCancel={() => setDtSyncVisible(false)}
        footer={
          <Button theme="solid" onClick={() => setDtSyncVisible(false)}>
            知道了
          </Button>
        }
        width={680}
      >
        {dtSyncMutation.isError ? (
          <p role="alert">
            {dtSyncErrorStatus === 503
              ? '钉钉通讯录连接尚未配置好，请联系管理员检查后再试。'
              : dtSyncErrorStatus === 502
                ? '这次没有读到钉钉通讯录，请稍后重试。'
                : '暂时无法确认同步结果。请稍后重试，如果仍然失败，请联系管理员。'}
          </p>
        ) : dtSyncResult?.disabled ? (
          <p>当前环境未开启钉钉账号同步，本次没有更新账号绑定。</p>
        ) : dtSyncResult && (
          <>
            <p>
              钉钉通讯录共 {dtSyncResult.total} 人，本次绑定 {dtSyncResult.bound} 人，之前已绑定 {dtSyncResult.already_bound} 人。
            </p>

            {(dtSyncResult?.unmatched ?? []).length > 0 ? (
              <div style={{ marginTop: 12 }}>
                <div style={{ fontWeight: 500, marginBottom: 6 }}>
                  没对上的人（{(dtSyncResult?.unmatched ?? []).length} 个）
                </div>
                <Table<DingTalkUnmatched>
                  columns={[
                    { title: '姓名', dataIndex: 'name', width: 100 },
                    { title: '钉钉通讯录部门', dataIndex: 'department', width: 140, render: (v?: string) => v || '-' },
                    { title: '手机', dataIndex: 'mobile', width: 130, render: (v: string | null) => <MaskedContact value={v} /> },
                    { title: '原因', dataIndex: 'reason_label' },
                  ]}
                  dataSource={dtSyncResult?.unmatched ?? []}
                  rowKey={(row?: DingTalkUnmatched) => row?.dingtalk_userid ?? `${row?.name ?? ''}|${row?.mobile ?? ''}`}
                  pagination={{ pageSize: 10, showSizeChanger: false }}
                  size="small"
                />
              </div>
            ) : null}

            {(dtSyncResult?.unbound_crm ?? []).length > 0 && (
              <div style={{ marginTop: 12 }}>
                <div style={{ fontWeight: 500, marginBottom: 6 }}>
                  尚未绑定钉钉的 CRM 账号（{dtSyncResult.unbound_crm.length} 个）
                </div>
                <div style={{ color: 'var(--crm-text-2)', fontSize: 12, lineHeight: 1.8 }}>
                  {(dtSyncResult?.unbound_crm ?? []).map((row) => (
                    <div key={row.id}>
                      {row.name}（{row.username}）
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </Modal>

      <Modal
        title="部门已按同序对齐"
        visible={Boolean(deptAlignResult)}
        onCancel={() => setDeptAlignResult(null)}
        footer={<Button theme="solid" onClick={() => setDeptAlignResult(null)}>知道了</Button>}
        width={620}
      >
        {deptAlignResult && <>
          <p>
            数据来源：同序 OA 目录；
            匹配 {deptAlignResult.matched} 人，本次变更 {deptAlignResult.changes.length} 人。
          </p>
          {deptAlignResult.changes.length === 0 ? (
            <p style={{ color: 'var(--crm-text-3)' }}>所有已匹配账号的部门都已经和同序一致。</p>
          ) : (
            <Table
              columns={[
                { title: '姓名', dataIndex: 'name', width: 110 },
                { title: '原部门', dataIndex: 'current_department', render: (v: string) => v || '未设置' },
                { title: '同序部门', dataIndex: 'dingtalk_department' },
              ]}
              dataSource={deptAlignResult.changes}
              rowKey="id"
              pagination={false}
              size="small"
            />
          )}
        </>}
      </Modal>

      <Modal
        title={`绑定同序人员 · ${oaBindingTarget?.name ?? ''}`}
        visible={Boolean(oaBindingTarget)}
        onCancel={() => setOaBindingTarget(null)}
        onOk={() => {
          if (oaBindingTarget) oaBindingMutation.mutate({ userId: oaBindingTarget.id, oaUserId: selectedOaMember })
        }}
        okText={selectedOaMember && selectedOaMember === oaBindingTarget?.oa_user_id ? '同步部门' : '保存绑定'}
        okButtonProps={{ disabled: selectedOaMember
          ? !oaMembersQuery.data?.some((member) => member.id === selectedOaMember)
          : !oaBindingTarget?.oa_user_id }}
        confirmLoading={oaBindingMutation.isPending}
      >
        <p>请核对姓名和工号。保存后会按同序更新该员工的 CRM 部门，并以所选人员的身份发起合同、打样和询价审批。按部门查看的数据会随归属变化。</p>
        <Select
          style={{ width: '100%' }}
          aria-label="同序人员"
          placeholder="选择同序人员"
          filter
          showClear
          loading={oaMembersQuery.isFetching}
          value={selectedOaMember ?? undefined}
          onChange={(value) => setSelectedOaMember(typeof value === 'string' ? value : null)}
          optionList={(oaMembersQuery.data ?? [])
            .filter((member) => member.crm_user_id === null || member.crm_user_id === oaBindingTarget?.id)
            .map((member) => ({ value: member.id, label: oaMemberLabel(member) }))}
        />
        {oaMembersQuery.isError && (
          <div style={{ display: 'grid', gap: 6, justifyItems: 'start' }}>
            <p style={{ color: 'var(--crm-error)', margin: 0 }}>暂时读不到同序人员列表。</p>
            <Button
              size="small"
              theme="borderless"
              style={{ paddingLeft: 0 }}
              onClick={() => void oaMembersQuery.refetch()}
            >
              重新读取同序人员
            </Button>
          </div>
        )}
        {oaBindingTarget?.oa_user_id && !oaMembersQuery.isError && !oaMembersQuery.isPending
          && !oaMembersQuery.data?.some((member) => member.id === oaBindingTarget.oa_user_id)
          && <p>原绑定的同序人员已不在在职名单。请选择新的同序人员。</p>}
      </Modal>

      {/* 用户新增/编辑 */}
      <Modal
        width={520}
        title={userEditTarget ? `编辑用户：${userEditTarget.name}` : '新建用户'}
        visible={userModal}
        onCancel={() => setUserModal(false)}
        onOk={() => userSaveMutation.mutate()}
        confirmLoading={userSaveMutation.isPending}
        okText="保存"
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {isAdmin && !userEditTarget && (
            <div>
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
                同序身份（可选）
              </div>
              <Select
                style={{ width: '100%' }}
                placeholder={oaMembersQuery.isPending ? '正在读取同序通讯录…' : '先选人；姓名和部门按同序带入'}
                filter
                showClear
                loading={oaMembersQuery.isPending}
                value={userForm.oa_user_id}
                onChange={(value) => {
                  const member = (oaMembersQuery.data ?? []).find((row) => row.id === value)
                  setUserForm({
                    ...userForm,
                    oa_user_id: (value as string) || undefined,
                    oa_department: member?.department ?? '',
                    name: member?.name ?? userForm.name,
                    department_id: undefined,
                  })
                }}
                optionList={(oaMembersQuery.data ?? []).filter((member) => member.crm_user_id === null).map((member) => ({
                  value: member.id,
                  label: oaMemberLabel(member),
                }))}
              />
              {oaMembersQuery.isError && (
                <div style={{ color: 'var(--crm-error)', fontSize: 12, marginTop: 4 }}>
                  暂时读不到同序通讯录，仍可手动填写姓名和部门。
                </div>
              )}
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginTop: 4 }}>
                选择后账号会绑定这个同序身份；所在部门按通讯录带入，CRM 没有时会按需补建，不导入整棵部门树。
              </div>
            </div>
          )}
          {/* 姓名和登录名都是短输入，竖着排把弹窗拉高了 70px；
              并排放两列（手机号、邮箱那排也是这么做的）。 */}
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <FormLabel required style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>姓名</FormLabel>
              <Input
                value={userForm.name}
                disabled={Boolean(userForm.oa_user_id)}
                onChange={(value) => setUserForm({ ...userForm, name: value })}
              />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
                <FormLabel required style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>登录名</FormLabel>
              </div>
              <Input
                value={userForm.username}
                disabled={Boolean(userEditTarget)}
                onChange={(value) => setUserForm({ ...userForm, username: value })}
              />
            </div>
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
              {userEditTarget ? '管理员重置密码（留空不重置）' : '初始密码 *'}
            </div>
            <Input
              mode="password"
              value={userForm.password}
              onChange={(value) => setUserForm({ ...userForm, password: value })}
              placeholder="至少 6 位"
              autoComplete="new-password"
            />
            {userEditTarget && (
              <div style={{ color: 'var(--crm-text-3)', fontSize: 12, marginTop: 6 }}>
                为当前选中的用户设置新密码，无需原密码。修改自己的密码也可从右上角头像进入「个人设置」。
              </div>
            )}
          </div>
          {/* 手机 / 邮箱两格删了（2026-11-19）：登录走账号密码，不发短信、不发邮件，
              填了也没人看。userForm 里这两个字段留着，编辑时按原值回传，
              不会把库里已有的手机号顺手清空。 */}
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
              部门{userForm.oa_user_id || userEditTarget?.department_managed_by_oa ? '（以同序为准）' : ''}
            </div>
            {userEditTarget?.department_managed_by_oa ? (
              <Input readOnly value={userEditTarget.department || '未设置部门'} />
            ) : userForm.oa_user_id ? (
              <Input readOnly value={userForm.oa_department || '同序身份未设部门'} />
            ) : (
              <Select
                style={{ width: '100%' }}
                placeholder="选择部门"
                showClear
                value={userForm.department_id}
                onChange={(value) =>
                  setUserForm({ ...userForm, department_id: (value as number) ?? undefined })
                }
                optionList={deptOptions}
              />
            )}
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>角色</div>
            <Select
              multiple
              style={{ width: '100%' }}
              placeholder="选择角色"
              value={userForm.role_ids}
              onChange={(value) => setUserForm({ ...userForm, role_ids: (value as number[]) ?? [] })}
              optionList={roleOptions}
            />
            {/* 这句原来塞在「角色」标签的括号里，把标签撑成 18 个字、跟上面几行
                标签长度全不一样。说明本来该在下拉框下面。 */}
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginTop: 6 }}>
              可多选；数据范围取所选角色里最大的一个。
            </div>
          </div>
        </div>
      </Modal>

      {/* 角色新增/编辑 */}
      <Modal
        title={roleEditTarget ? `编辑角色：${roleEditTarget.name}` : '新建角色'}
        visible={roleModal}
        onCancel={() => setRoleModal(false)}
        onOk={() => roleSaveMutation.mutate()}
        confirmLoading={roleSaveMutation.isPending}
        okText="保存"
        width={680}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
                角色编码<span className="required-mark" aria-hidden="true">*</span>{roleEditTarget ? '（不可修改）' : ''}
              </div>
              <Input
                value={roleForm.code}
                disabled={Boolean(roleEditTarget)}
                onChange={(value) => setRoleForm({ ...roleForm, code: value })}
                placeholder="如 salesperson"
              />
            </div>
            <div style={{ flex: 1 }}>
              <FormLabel required style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>角色名</FormLabel>
              <Input
                value={roleForm.name}
                onChange={(value) => setRoleForm({ ...roleForm, name: value })}
              />
            </div>
          </div>
          {/* 状态和数据范围都是两个字的下拉，各占一整行会把弹窗顶到 929px
              （1440×1100 的屏幕只剩 70px 余量，1366×768 的笔记本直接溢出）。
              并成一行，弹窗矮 60px，也跟上面「角色编码 / 角色名」形成同一节奏：短字段两列并排。 */}
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>状态</div>
              <Select
                style={{ width: '100%' }}
                value={roleForm.status}
                onChange={(value) => setRoleForm({ ...roleForm, status: value as string })}
                optionList={[
                  { value: 'active', label: '启用' },
                  { value: 'disabled', label: '停用（该角色不再参与权限与数据范围）' },
                ]}
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>数据范围</div>
              <Select
                style={{ width: '100%' }}
                value={roleForm.data_scope}
                onChange={(value) => setRoleForm({ ...roleForm, data_scope: value as string })}
                optionList={scopeOptions}
              />
            </div>
          </div>
          <div>
            <FormLabel style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>权限</FormLabel>
            {/* 按业务条线分组的多选（主人 2026-10-12：「太乱了…要找具体的权限要翻半天」）。
                原来是一个 `Select multiple`，业务员那条角色 23 个标签铺满五行、还得点「∨」
                展开；现在按模块成组、整组可勾、顶部可搜，并且自带滚动不撑长弹窗。 */}
            <PermissionPicker
              loading={permissionsQuery.isLoading}
              value={roleForm.permission_codes}
              onChange={(codes) => setRoleForm({ ...roleForm, permission_codes: codes })}
              options={(permissionsQuery.data ?? []).map((perm) => ({
                code: perm.code,
                name: perm.name,
                // `resource` 在接口里可空；空的归到"其他"，**不丢权限**
                resource: perm.resource ?? 'other',
                action: perm.action ?? '',
                // 「用户管理」是能改别人账号和权限的钥匙：勾上时在选项下面说清它到底能做什么，
                // 只作说明，不拦保存（2026-11-18 主人要求）
                note: perm.code === 'customer:pool_restore'
                  ? '与「公海回收复核」一起使用。可将数据范围内已回收的客户交还原负责人。'
                  : perm.code === 'user:manage'
                  ? '授予后，此人可以重置他人密码、分配管理员角色，以及修改角色权限和数据范围。请确认授权对象。'
                  : undefined,
              }))}
            />
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>说明</div>
            {/* 原来是个单行输入框 —— 一行高、右侧一大片空，写"这个角色干什么用"
                只能看见小半句。换成两行文本域，高度和上面的搜索框一致。 */}
            <TextArea
              rows={2}
              value={roleForm.description}
              onChange={(value) => setRoleForm({ ...roleForm, description: value })}
            />
          </div>
        </div>
      </Modal>

      {/* 部门新增/编辑 */}
      <Modal
        width={520}
        title={deptEditTarget ? `编辑部门：${deptEditTarget.name}` : '新建部门'}
        visible={deptModal}
        onCancel={() => setDeptModal(false)}
        onOk={() => deptSaveMutation.mutate()}
        confirmLoading={deptSaveMutation.isPending}
        okText="保存"
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <FormLabel required style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>部门名称</FormLabel>
            <Input
              value={deptForm.name}
              onChange={(value) => setDeptForm({ ...deptForm, name: value })}
            />
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
              上级部门（不选 = 顶级部门）
            </div>
            <Select
              style={{ width: '100%' }}
              placeholder="选择上级部门"
              showClear
              value={deptForm.parent_id}
              onChange={(value) => setDeptForm({ ...deptForm, parent_id: (value as number) ?? undefined })}
              optionList={deptOptions.filter((d) => d.value !== deptEditTarget?.id)}
            />
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginTop: 4 }}>
              不能选它自己，也不能选它的下级 —— 否则层级会绕成一个圈。
            </div>
          </div>
          <div>
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
              企业微信部门 ID（可选）
            </div>
            <Input
              value={deptForm.wecom_department_id}
              onChange={(value) => setDeptForm({ ...deptForm, wecom_department_id: value })}
            />
          </div>
        </div>
      </Modal>

      {/* 标签编辑 */}
      <Modal
        width={520}
        title="编辑标签"
        visible={tagEditTarget !== null}
        onCancel={() => setTagEditTarget(null)}
        onOk={() => updateTagMutation.mutate()}
        confirmLoading={updateTagMutation.isPending}
        okText="保存"
      >
        {tagEditTarget && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div>
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>
                标签名
              </div>
              <Input
                value={tagEditTarget.name}
                onChange={(value) => setTagEditTarget({ ...tagEditTarget, name: value })}
              />
            </div>
            <div>
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>分组</div>
              <Select
                style={{ width: '100%' }}
                value={tagEditTarget.type}
                onChange={(value) =>
                  setTagEditTarget({ ...tagEditTarget, type: value as string })
                }
                optionList={[
                  { value: 'custom', label: '自定义' },
                  { value: 'region', label: '区域' },
                  { value: 'industry', label: '行业' },
                  { value: 'grade', label: '等级' },
                  { value: 'channel', label: '渠道' },
                ]}
              />
            </div>
            <div>
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 4 }}>状态</div>
              <Select
                style={{ width: '100%' }}
                value={tagEditTarget.status}
                onChange={(value) =>
                  setTagEditTarget({ ...tagEditTarget, status: value as string })
                }
                optionList={[
                  { value: 'active', label: '启用' },
                  { value: 'disabled', label: '停用' },
                ]}
              />
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}

/**
 * 「报价审批分级」的就地编辑器（2026-10-14 主人拍板，替掉原来那个裸 JSON 输入框）。
 *
 * 后端把这份配置读成**一个数组**，然后从上往下找第一个「金额上限为空 或
 * 报价没超上限」的档（`quote/service.py`）。所以这里有三条不能想当然的规矩：
 *
 * 1. **表格顺序就是判定顺序**，它不是一组可以随便排的无序档位；
 * 2. **「不限」只能出现在最后一档** —— 中间留空一个，它后面的档永远轮不到，
 *    配的时候看不出来，报价一到就全落到中间那一档；
 * 3. 真正决定「谁能批」的只有角色码这一列（`approval/access.py` 拿它跟登录
 *    用户的角色求交集）；`node` 只是写进审批留痕的代号，界面上不给人看 ——
 *    老行沿用原值，新行补一个没被占用的。
 *
 * 另外：**角色留空 ≠ 没人能批，而是「谁有报价审批权限谁都能批」** ——
 * 后端那句判断是「配了角色才校验」，空数组等于不校验。这条得写在界面上，
 * 不然删空一个角色会以为只是"暂时没人管"。
 */
type ApprovalLevelDraft = {
  node: string
  label: string
  /**
   * 金额上限的**原始输入文本**，留空 = 不限。
   * 存文本而不是数字：存数字的话，输入 50000 的过程会被逐字符解析，
   * 5 → 50 → 500 一路跳，光标也不听话。
   */
  maxText: string
  roleCodes: string[]
}

/** 每档里字段名的小字样式。宽度固定，「第 1 档」「第 2 档」的输入框左边才对得齐。 */

/**
 * 金额上限的**唯一合法写法**：非负数字，最多两位小数。
 *
 * 留空不在这里判 —— 空串另有含义（= 不限），由调用方单独处理。
 *
 * 为什么非要有这条：只判「能不能转成数字」是不够的 —— `Number('1e5')`
 * 就是 100000，是合法数字，一路过关。而用户敲「5万」的时候心里想的是五万，
 * 任何"帮他把非法字符删掉"的做法都只会把值改错（见下面 onChange 的注释）。
 * 所以这里**只认这一种写法**，别的当场拒绝。
 */
const AMOUNT_TEXT_PATTERN = /^0*\d{1,14}(\.\d{1,2})?$/

function toApprovalDrafts(levels: Array<Record<string, unknown>>): ApprovalLevelDraft[] {
  return levels.map((item, index) => ({
    node: String(item.node ?? `level_${index + 1}`),
    label: String(item.label ?? ''),
    maxText: item.max_amount == null ? '' : String(item.max_amount),
    roleCodes: Array.isArray(item.role_codes) ? (item.role_codes as unknown[]).map(String) : [],
  }))
}

function ApprovalLevelsEditor({
  levels,
  saving,
  onSave,
}: {
  levels: Array<Record<string, unknown>>
  saving: boolean
  onSave: (levels: Array<Record<string, unknown>>) => void
}) {
  // 只在挂载时读一次 props：用户编辑期间父组件的 props 不会变（见上面 renderSettingValue
  // 里的 key 说明），所以不需要 effect 去同步 —— 同步反而会把没提交的改动冲掉。
  const [drafts, setDrafts] = useState<ApprovalLevelDraft[]>(() => toApprovalDrafts(levels))
  const rolesQuery = useQuery({ queryKey: ['system-roles'], queryFn: listRoles })

  // 角色下拉的候选：角色表里的中文名，再加上「配置里出现过、角色表里却没有」的
  // 孤儿码 —— 角色被删掉或改了码之后老配置会留下这种，直接吞掉的话用户在下拉里
  // 看不到它、保存时还可能被顺手抹掉。照原样列出来并标注。
  const roleOptions: Array<{ value: string; label: string }> = []
  const seenRoles = new Set<string>()
  for (const role of rolesQuery.data ?? []) {
    if (seenRoles.has(role.code)) continue
    seenRoles.add(role.code)
    roleOptions.push({ value: role.code, label: role.name })
  }
  for (const draft of drafts) {
    for (const code of draft.roleCodes) {
      if (seenRoles.has(code)) continue
      seenRoles.add(code)
      roleOptions.push({ value: code, label: `${code}（角色表里已经没有这个角色）` })
    }
  }

  const patch = (index: number, next: Partial<ApprovalLevelDraft>) =>
    setDrafts((rows) => rows.map((row, i) => (i === index ? { ...row, ...next } : row)))

  const addRow = () => {
    // 新行的内部代号取一个还没被占用的，避免和上面某行撞上（撞了会让留痕对不上）
    const used = new Set(drafts.map((row) => row.node))
    let seq = 1
    while (used.has(`level_${seq}`)) seq += 1
    setDrafts((rows) => [...rows, { node: `level_${seq}`, label: '', maxText: '', roleCodes: [] }])
  }

  const removeRow = (index: number) => setDrafts((rows) => rows.filter((_, i) => i !== index))

  const submit = () => {
    if (drafts.length === 0) {
      Toast.warning('至少要留一档，否则报价提交时找不到该找谁批')
      return
    }
    const next: Array<Record<string, unknown>> = []
    /* 上一档的金额上限，用来判「逐档递增」；null = 还没出现过上限（第一档或前面不限）。 */
    let prevMax: bigint | null = null
    let prevText = ''
    let prevLabel = ''
    for (let i = 0; i < drafts.length; i += 1) {
      const row = drafts[i]
      const label = row.label.trim()
      if (!label) {
        Toast.warning(`第 ${i + 1} 档还没填级别名称`)
        return
      }
      const text = row.maxText.trim()
      if (text === '') {
        if (i < drafts.length - 1) {
          Toast.warning(`「不限」只能放在最后一档：第 ${i + 1} 档留空之后，后面那些档永远轮不到`)
          return
        }
        next.push({ node: row.node, label, max_amount: null, role_codes: row.roleCodes })
        continue
      }
      // 格式严校。**不能只判「是不是数字」**：`Number('1e5')` 也是合法数字
      // （=100000），照样能混进来；更要紧的是别学以前那样先把非法字符删掉再判 ——
      // 那是把用户填错的值悄悄改成另一个"看起来对"的值，比直接报错危险得多。
      // 拒绝时把原值回显出来，用户才知道是哪一格、填了什么被挡下了。
      if (!AMOUNT_TEXT_PATTERN.test(text)) {
        Toast.warning(
          `第 ${i + 1} 档的「金额上限」填的是「${text}」，不是有效金额：` +
            '请输入最多 14 位整数、两位小数的金额。最后一档留空表示不限金额。',
        )
        return
      }
      const cents = paymentCents(text)
      if (prevMax !== null && cents <= prevMax) {
        Toast.warning(
          `第 ${i + 1} 档的金额上限（${text}）必须大于第 ${i} 档「${prevLabel}」的 ${prevText}：` +
            `审批按顺序匹配，上限不大于上一档时，第 ${i + 1} 档不会被使用。`,
        )
        return
      }
      prevMax = cents
      prevText = text
      prevLabel = label
      next.push({ node: row.node, label, max_amount: text, role_codes: row.roleCodes })
    }
    onSave(next)
  }

  return (
    // 一档一行（2026-11-19 重做）。原来一档是一张卡、卡里再挤两行：
    // 「级别 + 金额上限」一行、「角色」一行，右边留一截空，删除飘在右上角 ——
    // 一档两张行看不出"这是一条链上的一环"，三档就开始糊。
    // 现在：序号徽章定性（01/02/03），横着一行把 级别 / 金额上限 / 审批角色 排开，
    // 上下用同一套小标签对齐，删除压到最右、默认半透明。
    <div className="approval-chain">
      {drafts.map((row, index) => (
        <div className="approval-level" key={row.node}>
          <span className="approval-level-index">{String(index + 1).padStart(2, '0')}</span>

          <label className="approval-cell">
            <span className="approval-cell-label">级别名称</span>
            <Input
              size="small"
              value={row.label}
              placeholder="例如：销售主管"
              onChange={(value) => patch(index, { label: value })}
            />
          </label>

          <label className="approval-cell">
            <span className="approval-cell-label">金额上限（含）</span>
            <div className="approval-amount">
              {/* 口令写"≤"：这一档的意思是"报价不超过这个数就落到本档"，
                  一个符号比"金额上限"四个字更快看懂 */}
              <span className="approval-amount-op">≤</span>
              <Input
                size="small"
                value={row.maxText}
                placeholder="不限"
                // ⚠️ 原样收下用户敲的每一个字符，**不许在这里删字符**（2026-10-14 修）。
                // 静默洗字符会让「5万」变成「5」、用户看到的和存进库的不是一个值。
                onChange={(value) => patch(index, { maxText: value })}
                validateStatus={
                  row.maxText.trim() !== '' && !AMOUNT_TEXT_PATTERN.test(row.maxText.trim())
                    ? 'error'
                    : 'default'
                }
              />
              <span className="approval-amount-unit">元</span>
            </div>
          </label>

          <label className="approval-cell">
            <span className="approval-cell-label">审批角色</span>
            <Select
              multiple
              size="small"
              value={row.roleCodes}
              placeholder="留空 = 不限"
              maxTagCount={2}
              optionList={roleOptions}
              onChange={(value) =>
                patch(index, { roleCodes: ((value as Array<string | number>) ?? []).map(String) })
              }
            />
          </label>

          <button type="button" className="crm-text-action approval-level-del" onClick={() => removeRow(index)}>
            删除
          </button>
        </div>
      ))}

      <div className="approval-chain-actions">
        <button type="button" className="approval-add" onClick={addRow}>
          <span aria-hidden>+</span> 增加一档
        </button>
        <Button theme="solid" loading={saving} onClick={submit}>
          保存分档
        </Button>
        <span className="approval-chain-tip">从上往下匹配，先命中先结束</span>
      </div>

      <div className="approval-chain-hint">
        <b>怎么匹配：</b>从第一档往下找，第一个「报价金额 ≤ 上限」的档就是它。
        所以上限必须逐档递增 —— 相等或倒挂时当前这档会先命中，上面那档永远用不到。
        <b>两档留空规则：</b>「不限」只能放最后一档；角色留空不是没人能批，
        而是有报价审批权限的人都能批。
      </div>
    </div>
  )
}
