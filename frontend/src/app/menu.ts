/**
 * 左侧导航。
 *
 * 分组与图标沿用设计稿（销售作业 / 运营管理 / 智能与协同），
 * 未开发的模块标 ready: false，点进去会看到"规划中"的提示，而不是空白页。
 */

import type { ComponentType } from 'react'
import {
  IconAt,
  IconBarChartHStroked,
  IconBox,
  IconBriefcase,
  IconBulb,
  IconCheckList,
  IconComment,
  IconCreditCard,
  IconFile,
  IconFilter,
  IconFolder,
  IconGift,
  IconHome,
  IconInbox,
  IconMoneyExchangeStroked,
  IconPriceTag,
  IconSend,
  IconSetting,
  IconStar,
  IconTickCircle,
  IconUserGroup,
  IconVerify,
} from '@douyinfe/semi-icons'

export interface MenuItem {
  key: string
  path: string
  label: string
  icon: ComponentType
  ready: boolean
  /** 需要的权限码；字符串数组表示"任一命中即可"。缺省 = 所有人可见。 */
  permission?: string | string[]
  /**
   * 另一个身份看同一个入口时该叫什么。
   *
   * 例：`/settings` 对管理员是「系统设置」；而销售主管拿的是
   * `customer:pool_review`（公海回收复核），他进去本来就只看得到复核这一块，
   * 菜单上继续叫「系统设置」会让他以为拿到了整个系统设置。
   * `when` 命中且没有主权限时，用 `label` 显示（返修单 R12）。
   */
  altLabel?: { when: string; label: string }
}

export interface MenuGroup {
  title: string
  items: MenuItem[]
}

export const MENU_GROUPS: MenuGroup[] = [
  {
    title: '销售作业',
    items: [
      { key: 'workbench', path: '/workbench', label: '工作台', icon: IconHome, ready: true },
      { key: 'leads', path: '/leads', label: '线索中心', icon: IconFilter, ready: true, permission: 'lead:view' },
      { key: 'customers', path: '/customers', label: '客户中心', icon: IconUserGroup, ready: true, permission: 'customer:view' },
      { key: 'duplicate-cases', path: '/duplicate-cases', label: '撞单裁定', icon: IconVerify, ready: true, permission: 'customer:view' },
      {
        key: 'opportunities',
        path: '/opportunities',
        label: '商机中心',
        icon: IconMoneyExchangeStroked,
        ready: true,
        permission: 'opportunity:view',
      },
      { key: 'quotes', path: '/quotes', label: '报价中心', icon: IconFile, ready: true, permission: 'quote:view' },
      { key: 'orders', path: '/orders', label: '订单中心', icon: IconBox, ready: true, permission: 'order:view' },
      { key: 'samples', path: '/samples', label: '样品管理', icon: IconGift, ready: true, permission: 'sample:view' },
      { key: 'receivables', path: '/receivables', label: '回款中心', icon: IconCreditCard, ready: true, permission: 'payment:view' },
    ],
  },
  {
    title: '运营管理',
    items: [
      { key: 'products', path: '/products', label: '产品中心', icon: IconBriefcase, ready: true, permission: 'product:view' },
      // 价格中心 / 物流试算挂在 pricing 模块下，读接口用 product:view，维护用 price:manage
      { key: 'prices', path: '/prices', label: '价格中心', icon: IconPriceTag, ready: true, permission: ['price:manage', 'product:view'] },
      { key: 'logistics', path: '/logistics', label: '物流试算', icon: IconSend, ready: true, permission: ['price:manage', 'product:view'] },
      { key: 'tasks', path: '/tasks', label: '销售任务', icon: IconCheckList, ready: true, permission: 'task:view' },
      { key: 'approvals', path: '/approvals', label: '审批中心', icon: IconTickCircle, ready: true, permission: 'quote:view' },
      { key: 'documents', path: '/documents', label: '文档管理', icon: IconFolder, ready: true, permission: 'order:view' },
      { key: 'cases', path: '/cases', label: '案例库', icon: IconStar, ready: true, permission: 'quote:view' },
      { key: 'insights', path: '/insights', label: '新品洞察', icon: IconBulb, ready: true, permission: 'product:view' },
      // 这条原来叫「知识库」，但「知识库」是文档里的伞概念（新品洞察 → 定制需求
      // → 在产产品三类的合称），扣在这一条上名不副实。页面内里、编号规则、
      // 别处链接文案（「已转需求」「去看这条需求」）本来就都叫「定制需求」，
      // 只有标签和标题还挂着「知识库」——这次统一过来。编号前缀 XQ 正是
      // 「需求」的拼音首字母，改完两者才对得上。
      { key: 'inquiries', path: '/inquiries', label: '定制需求', icon: IconInbox, ready: true },
      {
        key: 'analytics',
        path: '/analytics',
        label: '数据分析',
        icon: IconBarChartHStroked,
        ready: true,
        // 各分析接口的权限不同，任一可看即给入口
        permission: ['customer:view', 'opportunity:view', 'quote:view', 'payment:view', 'product:view'],
      },
    ],
  },
  {
    title: '智能与协同',
    items: [
      { key: 'agent', path: '/agent', label: 'AI Sales Agent', icon: IconComment, ready: true, permission: 'agent:use' },
      { key: 'wecom', path: '/wecom', label: '企业微信', icon: IconAt, ready: true, permission: 'wecom:view' },
      {
        key: 'settings',
        path: '/settings',
        label: '系统设置',
        // 销售主管拿的是 `customer:pool_review`（公海回收复核），**不是**
        // `settings:manage`。原来这条只认 settings:manage，于是主管**常规菜单里
        // 根本没有入口**，只能靠回收预告的通知链接撞进去（返修单 R12）。
        // 现在两种权限任一命中都给入口，且**不给他系统设置权限本身**；
        // 没有 settings:manage 时菜单改名 —— 他进去本来就只看得到复核那一块。
        altLabel: { when: 'customer:pool_review', label: '公海回收复核' },
        icon: IconSetting,
        ready: true,
        permission: ['settings:manage', 'customer:pool_review'],
      },
    ],
  },
]

export const MENUS: MenuItem[] = MENU_GROUPS.flatMap((group) => group.items)

export function matchMenu(pathname: string): MenuItem | undefined {
  return MENUS.find((item) => pathname === item.path || pathname.startsWith(`${item.path}/`))
}

/** 与后端 require_permission 的判定对齐：admin 直通，数组权限任一命中即可。 */
function hasPermission(
  user: { roles: string[]; permissions?: string[] } | null,
  permission?: string | string[],
): boolean {
  if (!permission) return true
  if (!user) return false
  if (user.roles.includes('admin')) return true
  const granted = user.permissions ?? []
  const needed = Array.isArray(permission) ? permission : [permission]
  return needed.some((code) => granted.includes(code))
}

/** 菜单按当前用户权限过滤后的分组（权限不足的入口直接不显示）。 */
export function visibleMenuGroups(
  user: { roles: string[]; permissions?: string[] } | null,
): MenuGroup[] {
  return MENU_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => item.ready && hasPermission(user, item.permission)),
  })).filter((group) => group.items.length > 0)
}

/**
 * 当前用户看到的菜单名。
 *
 * 同一个入口对不同身份可能该叫不同名字：`/settings` 对管理员是「系统设置」，
 * 对只有公海回收复核权的主管是「公海回收复核」（返修单 R12）。
 * 判据：配了 `altLabel`、**没有主权限**、但 `altLabel.when` 命中。
 */
export function menuLabel(
  item: MenuItem,
  user: { roles: string[]; permissions?: string[] } | null,
): string {
  const alt = item.altLabel
  if (alt) {
    const primary = Array.isArray(item.permission) ? item.permission[0] : item.permission
    if (!hasPermission(user, primary) && hasPermission(user, alt.when)) return alt.label
  }
  return item.label
}
