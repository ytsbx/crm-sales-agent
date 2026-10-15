import { Button, Dropdown, Modal } from '@douyinfe/semi-ui'
import { useEffect, useState } from 'react'
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { IconIndentLeft, IconIndentRight } from '@douyinfe/semi-icons'

import TabBar from './TabBar'
import GlobalSearch from './GlobalSearch'
import NotificationBell from '../../modules/common/NotificationBell'
import CopilotDrawer from '../../modules/common/CopilotDrawer'
import { hasPermission, matchMenu, menuLabel, visibleMenuGroups } from '../menu'
import { logout } from '../../shared/api/auth'
import { useAuthStore } from '../../shared/store/auth'
import { useCurrentUserSync } from '../../shared/hooks/useCurrentUserSync'
import { useTabsStore } from '../../shared/store/tabs'
import { useCopilotStore } from '../../shared/store/copilot'

/** 独立页面（不在左侧菜单里）的页签名 */
const EXTRA_TABS: Record<string, string> = { '/pricing': '核价', '/order-drafts': '订单草稿' }

/** 角色代号 → 中文名 */
const ROLE_LABEL: Record<string, string> = {
  admin: '管理员',
  sales_manager: '销售主管',
  salesperson: '业务员',
  finance: '财务',
}

export default function AppLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const user = useAuthStore((state) => state.user)
  const clear = useAuthStore((state) => state.clear)
  const [credentialsDismissed, setCredentialsDismissed] = useState<number | null>(null)
  const openTab = useTabsStore((state) => state.openTab)
  const openCopilot = useCopilotStore((state) => state.openWith)
  // 移动端（场景23）：侧边栏变成抽屉，顶栏汉堡键开合
  const [siderOpen, setSiderOpen] = useState(false)
  // 桌面端侧边栏折叠：默认展开，状态记住，下次进来保持一致
  const [siderCollapsed, setSiderCollapsed] = useState(
    () => localStorage.getItem('sider-collapsed') === '1',
  )

  useEffect(() => {
    localStorage.setItem('sider-collapsed', siderCollapsed ? '1' : '0')
  }, [siderCollapsed])

  // 权限**跟着服务器走**：启动拉一次 + 每 60 秒一次（主人 2026-10-12 拍板）。
  // 从前只在登录时抄一次，于是管理员改权限后在线的人刷新也没用。
  useCurrentUserSync()
  const currentMenu = matchMenu(location.pathname)
  // 左侧菜单**不再按权限过滤**（主人 2026-10-12 拍板选 A）：
  // 入口留着，点进去由 RoutePermissionGuard 说明"该功能存在、只是你没权限"。
  // 从前直接隐藏入口，用户分不清"没权限"和"没做这个功能"。
  const menuGroups = visibleMenuGroups(user)
  const detailMatch = location.pathname.match(
    /^\/(customers|products|opportunities|quotes|orders|order-drafts)\/\d+$/,
  )
  const detailLabel = detailMatch
    ? {
        customers: '客户详情',
        products: '产品详情',
        opportunities: '商机详情',
        quotes: '报价详情',
        orders: '订单详情',
        'order-drafts': '订单草稿详情',
      }[detailMatch[1]]
    : null

  useEffect(() => {
    // 路由变化即收起抽屉：手机上点完菜单立刻看到页面
    setSiderOpen(false)
    if (detailLabel) {
      openTab({ key: location.pathname, label: detailLabel, path: location.pathname })
    } else if (EXTRA_TABS[location.pathname]) {
      openTab({
        key: location.pathname,
        label: EXTRA_TABS[location.pathname],
        path: location.pathname,
      })
    } else if (currentMenu) {
      openTab({ key: currentMenu.path, label: currentMenu.label, path: currentMenu.path })
    }
  }, [location.pathname, currentMenu, detailLabel, openTab])

  const handleLogout = () => {
    // 先取 token、再清状态：清完之后请求拦截器就取不到它了，
    // 而"让服务端作废这次登录"恰恰要靠它（第十批 10.12）。
    const token = useAuthStore.getState().token
    clear()
    useTabsStore.getState().closeAll()
    // 服务端作废是**尽力而为**的：网络不通也不能把人卡在已登出的界面上，
    // 所以不 await、不弹错。真发不出去时，那份凭据最迟在过期后自然失效。
    if (token) {
      void logout(token).catch(() => undefined)
    }
    navigate('/login')
  }

  const shellClass = [
    'app-shell',
    siderOpen && 'sider-open',
    siderCollapsed && 'sider-collapsed',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className={shellClass}>
      {siderOpen && (
        <div className="sider-backdrop" onClick={() => setSiderOpen(false)} />
      )}
      <aside className="app-sider">
        <button
          className="sider-close"
          type="button"
          aria-label="关闭菜单"
          onClick={() => setSiderOpen(false)}
        >
          ×
        </button>
        <div className="sider-brand">
          <img className="sider-logo" src="/tianchu-logo.svg" alt="" />
          <div className="sider-brand-text">
            <div className="sider-brand-name">天初CRM</div>
            <div className="sider-brand-sub">企业协同工作平台</div>
          </div>
          {/* 折叠开关固定在品牌行右侧：侧栏本身是滚动容器，
              放导航底部会被二十多个菜单项顶出屏幕，收起来就找不回来 */}
          <button
            className="sider-collapse-btn"
            type="button"
            aria-label={siderCollapsed ? '展开菜单' : '收起菜单'}
            title={siderCollapsed ? '展开菜单' : '收起菜单'}
            onClick={() => setSiderCollapsed((value) => !value)}
          >
            {siderCollapsed ? <IconIndentRight /> : <IconIndentLeft />}
          </button>
        </div>

        {menuGroups.map((group) => (
          <div key={group.title}>
            <div className="nav-group-title">{group.title}</div>
            {group.items.map((item) => {
              const Icon = item.icon
              const active = currentMenu?.key === item.key
              // 同一个入口对不同身份可能该叫不同名字（主管看到的是「公海回收复核」）
              const label = menuLabel(item, user)
              // 没权限的入口**照常可点**（点进去由 RoutePermissionGuard 说明原因），
              // 只做一点视觉区分；`title` 让悬停时就知道是怎么回事 ——
              // 但不改变"点得动"这件事，否则又回到"点了没反应"。
              const denied = !hasPermission(user, item.permission)
              const hint = denied ? `${label}（当前角色没有权限，点开有说明）` : label
              return (
                <Link
                  key={item.key}
                  to={item.path}
                  className={[
                    'nav-link',
                    active ? 'active' : '',
                    denied ? 'nav-link-denied' : '',
                  ].filter(Boolean).join(' ')}
                  title={siderCollapsed || denied ? hint : undefined}
                >
                  <span className="nav-icon">
                    <Icon />
                  </span>
                  <span>{label}</span>
                </Link>
              )
            })}
          </div>
        ))}

        <div className="sider-version" style={{ marginTop: 'auto', padding: '16px 18px', fontSize: 11, color: 'var(--crm-text-3)' }}>
          {/* 移动端顶栏收起退出链接，登出入口放这里（桌面仍走顶栏） */}
          <a
            className="sider-logout"
            onClick={handleLogout}
            style={{ display: 'none', fontSize: 13, color: 'var(--crm-text-2)', cursor: 'pointer', marginBottom: 8 }}
          >
            退出登录
          </a>
          <div>Semi Design 风格 · v1.1</div>
        </div>
      </aside>

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <header className="app-header">
          <button
            className="mobile-menu-btn"
            type="button"
            aria-label="打开菜单"
            onClick={() => setSiderOpen(true)}
          >
            ☰
          </button>
          <GlobalSearch />
          <div style={{ flex: 1 }} />
          <button className="copilot-trigger" type="button" onClick={() => openCopilot()}>
            <span className="copilot-dot" />
            AI 助手
          </button>
          <NotificationBell />
          <div className="header-user">
            {/* 头像这一块做成可点的下拉：以前它只是一张不会动的图，
                改密码 / 看权限全靠自己知道深链。现在「个人设置」就在手边。
                手机上顶栏的「退出」链接是收起来的（CSS），这个下拉就成了
                唯一的登出入口 —— 所以菜单里也要有退出。 */}
            <Dropdown
              trigger="click"
              position="bottomRight"
              render={
                <Dropdown.Menu>
                  <Dropdown.Item onClick={() => navigate('/settings?tab=profile')}>
                    个人设置
                  </Dropdown.Item>
                  <Dropdown.Item onClick={handleLogout}>退出登录</Dropdown.Item>
                </Dropdown.Menu>
              }
            >
              <div className="header-user-trigger" title="个人设置与退出">
                <div className="header-avatar">{user?.name?.slice(0, 1) ?? '?'}</div>
                <div className="header-user-meta" style={{ lineHeight: 1.25 }}>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{user?.name ?? '未登录'}</div>
                  <div style={{ fontSize: 11, color: 'var(--crm-text-3)' }}>
                    {user?.department ?? '未分配部门'} ·{' '}
                    {(user?.roles ?? [])
                      .map((role) => ROLE_LABEL[role] ?? role)
                      .join(' / ') || '无角色'}
                  </div>
                </div>
              </div>
            </Dropdown>
            <a className="header-logout" onClick={handleLogout} style={{ fontSize: 12, color: 'var(--crm-primary)', cursor: 'pointer' }}>
              退出
            </a>
          </div>
        </header>

        <TabBar />
        <div className="content-area">
          <Outlet />
        </div>
      </div>

      {/* 全局 Copilot 抽屉：任何页面都能拉开，自动带上当前页面的业务上下文 */}
      <CopilotDrawer />
      <Modal title="你的登录账号已生成" visible={Boolean(user?.initial_password_pending && credentialsDismissed !== user?.id)}
        onCancel={() => setCredentialsDismissed(user?.id ?? null)}
        footer={<><Button onClick={() => setCredentialsDismissed(user?.id ?? null)}>我知道了</Button>
          <Button theme="solid" onClick={() => { setCredentialsDismissed(user?.id ?? null); navigate('/settings?tab=profile') }}>去修改账号密码</Button></>}>
        <p>登录名：<strong style={{ overflowWrap: 'anywhere' }}>{user?.username}</strong></p>
        <p>初始密码：<strong>123456</strong></p>
        <p>以后可使用账号密码或钉钉扫码登录；在「我的」里可以自行修改（点头像也能进）。</p>
      </Modal>
    </div>
  )
}
