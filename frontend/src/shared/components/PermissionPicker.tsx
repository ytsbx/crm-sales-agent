import { useMemo, useState } from 'react'
import { Checkbox, Input } from '@douyinfe/semi-ui'

/**
 * 权限选择器：**按业务条线分组**，不再是 23 个标签糊成一片。
 *
 * 主人 2026-10-12 的反馈（原话）：「权限这里太乱了，我想要一目了然的感觉，
 * 排版也不好，要找具体的权限要翻半天」。
 *
 * ## 为什么这样改
 *
 * 原来是一个 `Select multiple`：选中的权限渲染成一堆标签（业务员那条角色
 * 23 个标签，铺满五行还得点「∨」展开），没选的在几百像素的下拉里平铺。
 * 两个后果：**看不出这批权限是干什么的**，以及**要找某一个得逐行扫**。
 *
 * 三处改法：
 *
 * 1. **分组**：权限在后端本来就带 `resource`（`customer` / `quote` / `order`…），
 *    按它编到「销售作业 / 运营管理 / 智能与协同」三个大类里 —— 与左侧菜单
 *    同一套分区，看权限的人和用系统的人看到的是同一张地图。
 * 2. **整组勾选**：绝大多数用法是"这个岗位要不要管报价"，
 *    按模块一次勾完，不用逐个点 5 次。组头显示 `已选 3/8`。
 * 3. **搜索**：要找具体某一个时直接打字（中英文都行，`quote:approve` 也能搜到），
 *    结果里仍标出它属于哪一组，不会"搜到了却不知道它在哪"。
 *
 * 另外：这一块自带滚动（`maxHeight`），**弹窗不会被权限撑长** ——
 * 原来 23 个标签会把弹窗顶得老高，说明和按钮都被推走。
 */

/** 一个权限（后端 `/permissions` 的返回，只列用得到的字段）。 */
export interface PermissionOption {
  code: string
  name: string
  resource: string
  action: string
}

/** 业务条线：权限归到哪一块。与左侧菜单的分区口径保持一致。 */
interface PermissionSection {
  title: string
  /** 这个条线包含哪些 `resource` */
  resources: string[]
}

export const PERMISSION_SECTIONS: PermissionSection[] = [
  {
    title: '销售作业',
    resources: [
      'lead',
      'customer',
      'opportunity',
      'quote',
      'order',
      'sample',
      'payment',
      'followup',
      'task',
      'file',
    ],
  },
  { title: '运营管理', resources: ['product', 'price', 'settings', 'audit'] },
  { title: '智能与协同', resources: ['agent', 'wecom'] },
  { title: '账号与权限', resources: ['user'] },
]

/** `resource` → 中文模块名（与权限名里的用词一致：`customer` → 「客户」）。 */
export const PERMISSION_RESOURCE_LABEL: Record<string, string> = {
  lead: '线索',
  customer: '客户',
  opportunity: '商机',
  quote: '报价',
  order: '订单',
  sample: '样品',
  payment: '回款',
  followup: '跟进',
  task: '任务',
  file: '文件',
  product: '产品',
  price: '价格',
  settings: '系统设置',
  audit: '审计日志',
  agent: 'AI 助手',
  wecom: '企业微信',
  user: '账号与权限',
}

/** 认不出的 `resource` 归到这里（新模块上线时不会凭空消失）。 */
export const UNGROUPED_TITLE = '其他权限'

function resourceLabel(resource: string): string {
  return PERMISSION_RESOURCE_LABEL[resource] ?? resource
}

interface Props {
  options: PermissionOption[]
  value: string[]
  onChange: (codes: string[]) => void
  loading?: boolean
}

/**
 * ⚠️ **故意没有 `disabled` 这一项**：角色弹窗打开就一定能改权限，
 * 没有"只读地看权限"这种场景。先前我凭想象留了一个 `disabled` 入口，
 * 结果被 `ops/check_disabled_hint.py` 抓住 —— 那个检查的口径是
 * 「有禁用控件就要说明为什么禁」（主人本轮最初的抱怨就是这个），
 * 而一个永远没人传的 `disabled` 只会让下一个人以为"这里会被禁"。
 * 真需要只读展示时再加，并想好它对用户说什么。
 */
export default function PermissionPicker({ options, value, onChange, loading }: Props) {
  const [keyword, setKeyword] = useState('')
  const selected = useMemo(() => new Set(value), [value])

  // 按条线 → 模块 分组。顺序按 PERMISSION_SECTIONS 固定，认不出的资源排在最后，
  // **不丢**（宁可多一个"其他权限"分组，也不要让某个权限在新界面上消失）
  const sections = useMemo(() => {
    const known = new Set(PERMISSION_SECTIONS.flatMap((section) => section.resources))
    const out = PERMISSION_SECTIONS.map((section) => ({
      title: section.title,
      groups: section.resources
        .map((resource) => ({
          resource,
          label: resourceLabel(resource),
          items: options.filter((option) => option.resource === resource),
        }))
        .filter((group) => group.items.length > 0),
    })).filter((section) => section.groups.length > 0)

    const rest = options.filter((option) => !known.has(option.resource))
    if (rest.length) {
      const byResource = new Map<string, PermissionOption[]>()
      for (const option of rest) {
        byResource.set(option.resource, [...(byResource.get(option.resource) ?? []), option])
      }
      out.push({
        title: UNGROUPED_TITLE,
        groups: [...byResource.entries()].map(([resource, items]) => ({
          resource,
          label: resourceLabel(resource),
          items,
        })),
      })
    }
    return out
  }, [options])

  const trimmed = keyword.trim().toLowerCase()
  const matched = useMemo(() => {
    if (!trimmed) return null
    return options.filter(
      (option) =>
        option.name.toLowerCase().includes(trimmed) ||
        option.code.toLowerCase().includes(trimmed) ||
        resourceLabel(option.resource).includes(trimmed),
    )
  }, [options, trimmed])

  const toggle = (code: string, checked: boolean) => {
    const next = new Set(value)
    if (checked) next.add(code)
    else next.delete(code)
    // 顺序按 options 走，避免每次勾选都让"已选"数组顺序乱跳（保存的是集合，但看着乱）
    onChange(options.filter((option) => next.has(option.code)).map((option) => option.code))
  }

  const toggleGroup = (codes: string[], checked: boolean) => {
    const next = new Set(value)
    for (const code of codes) {
      if (checked) next.add(code)
      else next.delete(code)
    }
    onChange(options.filter((option) => next.has(option.code)).map((option) => option.code))
  }

  const total = options.length
  const picked = options.filter((option) => selected.has(option.code)).length

  return (
    <div className="permission-picker">
      <div className="permission-picker-bar">
        <Input
          size="small"
          prefix="搜索"
          placeholder="打中文或权限码都行，例如「报价」或 quote:approve"
          value={keyword}
          onChange={setKeyword}
        />
        <span className="permission-picker-count">
          已选 {picked} / {total}
        </span>
      </div>

      <div className="permission-picker-body" role="group" aria-label="权限">
        {loading && <div className="permission-picker-empty">权限加载中…</div>}

        {/* 搜索态：**保留分组名**，免得"搜到了却不知道它属于哪一块" */}
        {!loading && matched && (
          <>
            {matched.length === 0 ? (
              <div className="permission-picker-empty">
                没有匹配「{keyword.trim()}」的权限。换个词试试，或者清空搜索看全部。
              </div>
            ) : (
              matched.map((option) => (
                <label key={option.code} className="permission-picker-row">
                  <Checkbox
                    checked={selected.has(option.code)}
                    onChange={(event) => toggle(option.code, Boolean(event.target.checked))}
                  />
                  <span className="permission-picker-name">{option.name}</span>
                  <span className="permission-picker-code">
                    {resourceLabel(option.resource)} · {option.code}
                  </span>
                </label>
              ))
            )}
          </>
        )}

        {/* 分组态 */}
        {!loading &&
          !matched &&
          sections.map((section) => (
            <div key={section.title} className="permission-picker-section">
              <div className="permission-picker-section-title">{section.title}</div>
              {section.groups.map((group) => {
                const codes = group.items.map((item) => item.code)
                const groupPicked = codes.filter((code) => selected.has(code)).length
                const allPicked = groupPicked === codes.length
                return (
                  <div key={group.resource} className="permission-picker-group">
                    <div className="permission-picker-group-head">
                      <span className="permission-picker-group-name">{group.label}</span>
                      <span className="permission-picker-group-count">
                        {groupPicked}/{codes.length}
                      </span>
                      <button
                        type="button"
                        className="permission-picker-link"
                        onClick={() => toggleGroup(codes, !allPicked)}
                      >
                        {allPicked ? '清空本组' : '全选本组'}
                      </button>
                    </div>
                    <div className="permission-picker-grid">
                      {group.items.map((option) => (
                        <label key={option.code} className="permission-picker-row" title={option.code}>
                          <Checkbox
                            checked={selected.has(option.code)}
                            onChange={(event) =>
                              toggle(option.code, Boolean(event.target.checked))
                            }
                          />
                          <span className="permission-picker-name">{option.name}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>
          ))}
      </div>
    </div>
  )
}
