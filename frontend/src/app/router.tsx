import type { ReactNode } from 'react'
import { createBrowserRouter, Navigate } from 'react-router-dom'

import AppLayout from './layout/AppLayout'
import LoginPage from '../modules/auth/LoginPage'
import CustomerDetailPage from '../modules/customer/CustomerDetailPage'
import CustomerListPage from '../modules/customer/CustomerListPage'
import LeadListPage from '../modules/lead/LeadListPage'
import LogisticsPage from '../modules/logistics/LogisticsPage'
import OpportunityDetailPage from '../modules/opportunity/OpportunityDetailPage'
import OpportunityListPage from '../modules/opportunity/OpportunityListPage'
import OrderDetailPage from '../modules/order/OrderDetailPage'
import OrderListPage from '../modules/order/OrderListPage'
import ApprovalPage from '../modules/approval/ApprovalPage'
import AnalyticsPage from '../modules/analytics/AnalyticsPage'
import AgentPage from '../modules/agent/AgentPage'
import ProductDetailPage from '../modules/product/ProductDetailPage'
import ProductListPage from '../modules/product/ProductListPage'
import PriceCenterPage from '../modules/pricing/PriceCenterPage'
import PricingPage from '../modules/pricing/PricingPage'
import QuoteDetailPage from '../modules/quote/QuoteDetailPage'
import QuoteListPage from '../modules/quote/QuoteListPage'
import SampleListPage from '../modules/sample/SampleListPage'
import SettingsPage from '../modules/settings/SettingsPage'
import TaskListPage from '../modules/task/TaskListPage'
import WorkbenchPage from '../modules/workbench/WorkbenchPage'
import { useAuthStore } from '../shared/store/auth'

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
      { path: 'receivables', element: <OrderListPage initialTab="receivables" /> },
      {
        path: 'knowledge',
        element: (
          <div className="page-container">
            <h2 className="page-title">知识库</h2>
            <p className="page-subtitle">产品资料、销售 SOP、常见问题，供 AI 检索引用</p>
            <div className="placeholder-box">
              知识库排在 Phase 6 之后：需要接本机的 Qdrant 向量库与文档解析能力。
            </div>
          </div>
        ),
      },
      {
        path: 'wecom',
        element: (
          <div className="page-container">
            <h2 className="page-title">企业微信</h2>
            <p className="page-subtitle">外部联系人同步、待归一、离职继承</p>
            <div className="placeholder-box">
              企业微信集成尚未开发：需要 corp id / secret 与公网回调地址。
            </div>
          </div>
        ),
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
