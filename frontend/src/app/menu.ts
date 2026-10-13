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
  IconDelete,
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
      // 回收站：三块（线索/产品/客户）集中在一个页面。任一业务模块可看即给入口，
      // 页面内部再按各自权限决定显示哪几块（与"数据分析"同一套写法）。
      {
        key: 'recycle-bin',
        path: '/recycle-bin',
        label: '回收站',
        icon: IconDelete,
        ready: true,
        permission: ['lead:view', 'product:view', 'customer:view'],
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

/**
 * 与后端 require_permission 的判定对齐：admin 直通，数组权限任一命中即可。
 *
 * ⚠️ **入参的字段一律当"可能没有"处理**（2026-10-13）：
 * 登录态是**持久化**的（`crm-auth`），旧版本留下的那份数据会被新版代码直接读出来用。
 * 前一版这里写 `user.roles.includes('admin')`，而登录接口 `_user_brief` 只返回
 * `{id, name, username}`（没有 roles）——两者一叠加，只要本地存的是那份不完整的 user，
 * **整个 AppLayout（每一页）都会崩**，界面上出现的是 React 的英文报错和调用栈
 * （`Cannot read properties of undefined (reading 'includes')` + `at hasPermission`），
 * 排查脚本实测复现过。
 *
 * TypeScript 的类型标注（`roles: string[]`）**拦不住运行时**：数据是从 localStorage
 * 读出来的 JSON，类型只是"我以为的形状"。所以这里用局部变量 + `?? []` 兜底，
 * 缺字段就按"没有这个权限"处理（与 `!user` 同一口径：宁可少给权限，不可崩页面）。
 */
export function hasPermission(
  user: { roles?: string[]; permissions?: string[] } | null | undefined,
  permission?: string | string[],
): boolean {
  if (!permission) return true
  if (!user) return false
  const roles = user.roles ?? []
  if (roles.includes('admin')) return true
  const granted = user.permissions ?? []
  const needed = Array.isArray(permission) ? permission : [permission]
  return needed.some((code) => granted.includes(code))
}

/**
 * 菜单分组（**不再按权限过滤**，主人 2026-10-12 拍板）。
 *
 * ## 为什么改了
 *
 * 从前这里 `filter(hasPermission)` —— 权限不足的入口**整个不显示**。
 * 于是"管理员把某个角色的权限收回"之后，那个人看到的菜单里那一项**消失了**，
 * 他分不清是「我没有这个权限」还是「系统没有这个功能」。
 *
 * 主人选的是：**入口留着**，点进去由 `RoutePermissionGuard` 把原因说明白
 * （"该功能存在，只是你这个角色看不到它"）。判断仍然用同一份 `hasPermission`。
 *
 * ⚠️ `ready: false` 的项照旧不显示 —— 那是"功能还没做"，与权限无关，
 * 两件事不能混：一个该说"你没权限"，一个本就该说"还没做"。
 */
export function visibleMenuGroups(_user: { roles: string[]; permissions?: string[] } | null): MenuGroup[] {
  // 参数保留：调用方还在传，而且将来要按"新功能标记"之类做区分时还会用到。
  // 现在不参与过滤，所以显式写成 `_user`，免得读者以为这里在按权限筛。
  return MENU_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => item.ready),
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
