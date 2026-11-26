import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { ConfigProvider, Toast } from '@douyinfe/semi-ui'
import zh_CN from '@douyinfe/semi-ui/lib/es/locale/source/zh_CN'
import { RouterProvider } from 'react-router-dom'

// React 19 需要先引入 Semi 适配器，再使用任何 Semi 组件
import '@douyinfe/semi-ui/react19-adapter'
// 注意：Semi 的 package.json exports 没有导出 dist 目录，
// 用裸包名引 CSS 会被 Vite 拒绝，所以这里直接指到 node_modules 里的文件。
import '../node_modules/@douyinfe/semi-ui/dist/css/semi.min.css'
import './index.css'

import { router } from './app/router'
import { installToastGuard } from './shared/toast-guard'
import { queryClient } from './shared/query-client'
import { ApiError } from './shared/api/client'
import { oaExchange } from './shared/api/auth'
import { useAuthStore } from './shared/store/auth'

// 提示条兜底闸：必须在任何页面渲染前装好，否则首批提示可能还是空白的
installToastGuard()

/** OA 软件内嵌打开时，URL 片段带一次性票据；先用它换登录凭证，再把票据从地址栏去掉。 */
async function consumeOaTicket() {
  const params = new URLSearchParams(window.location.search)
  const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, ''))
  const ticket = params.get('oa_ticket') ?? hashParams.get('oa_ticket')
  if (!ticket) return
  params.delete('oa_ticket')
  hashParams.delete('oa_ticket')
  const query = params.toString()
  const hash = hashParams.toString()
  window.history.replaceState(
    {},
    '',
    `${window.location.pathname}${query ? `?${query}` : ''}${hash ? `#${hash}` : ''}`,
  )
  try {
    const result = await oaExchange(ticket)
    useAuthStore.getState().setAuth(result.access_token, null)
  } catch (error) {
    sessionStorage.setItem(
      'crm-oa-sso-error',
      error instanceof ApiError ? error.message : '同序登录失败，请重新打开',
    )
  }
}

void consumeOaTicket().finally(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <ConfigProvider locale={zh_CN}>
          <RouterProvider router={router} />
        </ConfigProvider>
      </QueryClientProvider>
    </StrictMode>,
  )
  window.setTimeout(() => {
    const message = sessionStorage.getItem('crm-oa-sso-error')
    if (message) {
      sessionStorage.removeItem('crm-oa-sso-error')
      Toast.error(message)
    }
  }, 200)
})
