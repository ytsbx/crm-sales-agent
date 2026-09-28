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
  IconBook,
  IconBriefcase,
  IconCheckList,
  IconComment,
  IconCreditCard,
  IconFile,
  IconFilter,
  IconGift,
  IconHome,
  IconMoneyExchangeStroked,
  IconOrderedList,
  IconPriceTag,
  IconSend,
  IconSetting,
  IconTickCircle,
  IconUserGroup,
} from '@douyinfe/semi-icons'

export interface MenuItem {
  key: string
  path: string
  label: string
  icon: ComponentType
  ready: boolean
  /** 需要的权限码；字符串数组表示"任一命中即可"。缺省 = 所有人可见。 */
  permission?: string | string[]
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
      {
        key: 'opportunities',
        path: '/opportunities',
        label: '商机中心',
        icon: IconMoneyExchangeStroked,
        ready: true,
        permission: 'opportunity:view',
      },
      { key: 'quotes', path: '/quotes', label: '报价中心', icon: IconFile, ready: true, permission: 'quote:view' },
      { key: 'orders', path: '/orders', label: '订单中心', icon: IconOrderedList, ready: true, permission: 'order:view' },
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
      { key: 'documents', path: '/documents', label: '文档管理', icon: IconFile, ready: true, permission: 'order:view' },
      { key: 'cases', path: '/cases', label: '案例库', icon: IconTickCircle, ready: true, permission: 'quote:view' },
      { key: 'insights', path: '/insights', label: '新品洞察', icon: IconBriefcase, ready: true, permission: 'product:view' },
      { key: 'knowledge', path: '/knowledge', label: '知识库', icon: IconBook, ready: true },
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
      { key: 'settings', path: '/settings', label: '系统设置', icon: IconSetting, ready: true, permission: 'settings:manage' },
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
