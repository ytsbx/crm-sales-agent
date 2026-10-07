import type { ReactNode } from 'react'
import { createBrowserRouter, Navigate } from 'react-router-dom'

import AppLayout from './layout/AppLayout'
import { lazyPage } from './lazyPage'
import { useAuthStore } from '../shared/store/auth'

// ---------------------------------------------------------------------------
// 页面按需加载（第九批 §9.11）
//
// 这里此前是 33 条**同步 import**，构建成一个 1.79 MB 的单文件：进登录页也要
// 先下载报价中心、洞察、知识库的全部代码。改成 `lazyPage` 之后，每个页面各自
// 成块（`app/lazyPage.tsx` 里统一带了加载态、失败重试与"发版后旧资源"的处理），
// 图表、Excel 等重依赖只跟着用到它们的页面走。
//
// 注意：同一个页面组件被多条路由复用（例：`OrderListPage` 同时服务
// `/orders` 与 `/receivables`），`lazyPage` 对同一个 loader 只会取一次块。
// ---------------------------------------------------------------------------
const LoginPage = lazyPage(() => import('../modules/auth/LoginPage'))
const WorkbenchPage = lazyPage(() => import('../modules/workbench/WorkbenchPage'))
const CustomerListPage = lazyPage(() => import('../modules/customer/CustomerListPage'))
const CustomerDetailPage = lazyPage(() => import('../modules/customer/CustomerDetailPage'))
const DuplicateCasePage = lazyPage(() => import('../modules/customer/DuplicateCasePage'))
const LeadListPage = lazyPage(() => import('../modules/lead/LeadListPage'))
const OpportunityListPage = lazyPage(() => import('../modules/opportunity/OpportunityListPage'))
const OpportunityDetailPage = lazyPage(() => import('../modules/opportunity/OpportunityDetailPage'))
const QuoteListPage = lazyPage(() => import('../modules/quote/QuoteListPage'))
const QuoteDetailPage = lazyPage(() => import('../modules/quote/QuoteDetailPage'))
const ApprovalPage = lazyPage(() => import('../modules/approval/ApprovalPage'))
const OrderListPage = lazyPage(() => import('../modules/order/OrderListPage'))
const OrderDetailPage = lazyPage(() => import('../modules/order/OrderDetailPage'))
const OrderDraftsPage = lazyPage(() => import('../modules/order/OrderDraftsPage'))
const KnowledgePage = lazyPage(() => import('../modules/knowledge/KnowledgePage'))
const DocumentsPage = lazyPage(() => import('../modules/contract/DocumentsPage'))
const CasesPage = lazyPage(() => import('../modules/cases/CasesPage'))
const ProductInsightsPage = lazyPage(() => import('../modules/product/ProductInsightsPage'))
const WeComPage = lazyPage(() => import('../modules/wecom/WeComPage'))
const ProductListPage = lazyPage(() => import('../modules/product/ProductListPage'))
const ProductDetailPage = lazyPage(() => import('../modules/product/ProductDetailPage'))
const PriceCenterPage = lazyPage(() => import('../modules/pricing/PriceCenterPage'))
const PricingPage = lazyPage(() => import('../modules/pricing/PricingPage'))
const LogisticsPage = lazyPage(() => import('../modules/logistics/LogisticsPage'))
const SampleListPage = lazyPage(() => import('../modules/sample/SampleListPage'))
const TaskListPage = lazyPage(() => import('../modules/task/TaskListPage'))
const AnalyticsPage = lazyPage(() => import('../modules/analytics/AnalyticsPage'))
const AgentPage = lazyPage(() => import('../modules/agent/AgentPage'))
const SettingsPage = lazyPage(() => import('../modules/settings/SettingsPage'))

function RequireAuth({ children }: { children: ReactNode }) {
  const token = useAuthStore((state) => state.token)
  if (!token) {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}

export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  {
    path: '/',
    element: (
      <RequireAuth>
        <AppLayout />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Navigate to="/workbench" replace /> },
      { path: 'workbench', element: <WorkbenchPage /> },
      { path: 'customers', element: <CustomerListPage /> },
      { path: 'duplicate-cases', element: <DuplicateCasePage /> },
      { path: 'customers/:id', element: <CustomerDetailPage /> },
      {
        path: 'leads',
        element: <LeadListPage />,
      },
      {
        path: 'opportunities',
        element: <OpportunityListPage />,
      },
      { path: 'opportunities/:id', element: <OpportunityDetailPage /> },
      {
        path: 'quotes',
        element: <QuoteListPage />,
      },
      { path: 'quotes/:id', element: <QuoteDetailPage /> },
      { path: 'approvals', element: <ApprovalPage /> },
      {
        path: 'orders',
        element: <OrderListPage />,
      },
      { path: 'orders/:id', element: <OrderDetailPage /> },
      { path: 'order-drafts', element: <OrderDraftsPage /> },
      { path: 'order-drafts/:id', element: <OrderDraftsPage /> },
      { path: 'receivables', element: <OrderListPage initialTab="receivables" /> },
      { path: 'inquiries', element: <KnowledgePage /> },
      { path: 'documents', element: <DocumentsPage /> },
      { path: 'cases', element: <CasesPage /> },
      { path: 'insights', element: <ProductInsightsPage /> },
      {
        path: 'wecom',
        element: <WeComPage />,
      },
      {
        path: 'products',
        element: <ProductListPage />,
      },
      { path: 'products/:id', element: <ProductDetailPage /> },
      {
        path: 'prices',
        element: <PriceCenterPage />,
      },
      { path: 'pricing', element: <PricingPage /> },
      { path: 'logistics', element: <LogisticsPage /> },
      { path: 'samples', element: <SampleListPage /> },
      // 深链：点样品编号 / 从案例证据跳进来时直接打开该条详情
      { path: 'samples/:id', element: <SampleListPage /> },
      {
        path: 'tasks',
        element: <TaskListPage />,
      },
      {
        path: 'analytics',
        element: <AnalyticsPage />,
      },
      {
        path: 'agent',
        element: <AgentPage />,
      },
      {
        path: 'settings',
        element: <SettingsPage />,
      },
    ],
  },
  { path: '*', element: <Navigate to="/workbench" replace /> },
])
