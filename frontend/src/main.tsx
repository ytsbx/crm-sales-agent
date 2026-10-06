import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfigProvider } from '@douyinfe/semi-ui'
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

// 提示条兜底闸：必须在任何页面渲染前装好，否则首批提示可能还是空白的
installToastGuard()

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ConfigProvider locale={zh_CN}>
        <RouterProvider router={router} />
      </ConfigProvider>
    </QueryClientProvider>
  </StrictMode>,
)
