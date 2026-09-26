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
      { key: 'leads', path: '/leads', label: '线索中心', icon: IconFilter, ready: true },
      { key: 'customers', path: '/customers', label: '客户中心', icon: IconUserGroup, ready: true },
      {
        key: 'opportunities',
        path: '/opportunities',
        label: '商机中心',
        icon: IconMoneyExchangeStroked,
        ready: true,
      },
      { key: 'quotes', path: '/quotes', label: '报价中心', icon: IconFile, ready: true },
      { key: 'orders', path: '/orders', label: '订单中心', icon: IconOrderedList, ready: true },
      { key: 'samples', path: '/samples', label: '样品管理', icon: IconGift, ready: true },
      { key: 'receivables', path: '/receivables', label: '回款中心', icon: IconCreditCard, ready: true },
    ],
  },
  {
    title: '运营管理',
    items: [
      { key: 'products', path: '/products', label: '产品中心', icon: IconBriefcase, ready: true },
      { key: 'prices', path: '/prices', label: '价格中心', icon: IconPriceTag, ready: true },
      { key: 'logistics', path: '/logistics', label: '物流试算', icon: IconSend, ready: true },
      { key: 'tasks', path: '/tasks', label: '销售任务', icon: IconCheckList, ready: true },
      { key: 'approvals', path: '/approvals', label: '审批中心', icon: IconTickCircle, ready: true },
      { key: 'knowledge', path: '/knowledge', label: '知识库', icon: IconBook, ready: false },
      {
        key: 'analytics',
        path: '/analytics',
        label: '数据分析',
        icon: IconBarChartHStroked,
        ready: true,
      },
    ],
  },
  {
    title: '智能与协同',
    items: [
      { key: 'agent', path: '/agent', label: 'AI Sales Agent', icon: IconComment, ready: true },
      { key: 'wecom', path: '/wecom', label: '企业微信', icon: IconAt, ready: false },
      { key: 'settings', path: '/settings', label: '系统设置', icon: IconSetting, ready: true },
    ],
  },
]

export const MENUS: MenuItem[] = MENU_GROUPS.flatMap((group) => group.items)

export function matchMenu(pathname: string): MenuItem | undefined {
  return MENUS.find((item) => pathname === item.path || pathname.startsWith(`${item.path}/`))
}
