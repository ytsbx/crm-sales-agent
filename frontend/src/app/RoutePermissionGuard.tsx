import { Link, Outlet, useLocation } from 'react-router-dom'
import { Button } from '@douyinfe/semi-ui'

import { hasPermission, matchMenu, menuLabel } from './menu'
import { useAuthStore } from '../shared/store/auth'

/**
 * 路由级权限守卫：**入口留着，点进来把原因说清楚**（主人 2026-10-12 拍板选 A）。
 *
 * ## 为什么需要它
 *
 * 从前菜单是按权限**过滤**的（权限不足的入口直接不显示），所以"点进没权限的页面"
 * 这条路走不到 —— 也就一直没做路由守卫。主人拍板改成"入口留着、点了说明原因"，
 * 于是必须补上这道守卫，否则会出现**更糟的**结果：页面渲染出来、接口全 403、
 * 满屏报错或一片空白，用户只看到"系统坏了"。
 *
 * ## 为什么选"留着入口"（而不是继续隐藏）
 *
 * 隐藏入口时，用户看到的现象是「**菜单里没有这一项**」—— 他分不清是
 * "我没有这个权限"还是"系统压根没做这个功能"。主人的原话就是这个意思：
 * 「用户知道"这功能存在、我被收回了"，而不是以为"系统没这个功能"——
 * 跟您一开始抱怨的"点了没反应还不说为什么"是同一个道理」。
 *
 * ## 判据只有一份
 *
 * 权限判断复用 `menu.ts` 的 `hasPermission`（admin 直通、数组任一命中），
 * 与后端 `require_permission` 同口径。**这里只是把话说清楚，真正的拦截在后端** ——
 * 就算有人绕过这个组件直接调接口，后端照样 403。
 */
export default function RoutePermissionGuard() {
  const user = useAuthStore((state) => state.user)
  const location = useLocation()
  const menu = matchMenu(location.pathname)

  // 没有对应菜单项的页面（详情页、工作台等）不在这里判：
  // 详情页的权限由它自己的接口决定，判错了会挡住正常流程
  // 设置页包含向全部登录用户开放的「我的」，管理页签由 SettingsPage 自行限制。
  // 菜单权限在这里仅用于区分名称，不能用它拦住个人账号入口。
  if (!menu || menu.key === 'settings' || hasPermission(user, menu.permission)) {
    // 无权限路由的**子页面**（详情页等）继承这层的判定，正常放行
    return <Outlet />
  }

  const label = menuLabel(menu, user)
  return (
    <div className="page-container">
      <div className="permission-denied">
        <div className="permission-denied-title">没有访问「{label}」的权限</div>
        <p className="permission-denied-body">
          你的账号当前没有开通这个模块的权限（也可能是刚被收回）。
          这不是系统故障 —— 该功能存在，只是你这个角色看不到它。
        </p>
        <p className="permission-denied-hint">
          需要用的话，请找管理员在「系统设置 › 角色与数据范围」里给你所在的角色勾上；
          权限改完「刷新本页」即可生效，不需要重新登录。
        </p>
        <div className="permission-denied-actions">
          <Link to="/workbench">
            <Button theme="solid" type="primary">
              回到工作台
            </Button>
          </Link>
        </div>
      </div>
    </div>
  )
}
