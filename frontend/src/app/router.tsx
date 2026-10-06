import OrderDraftsPage from '../modules/order/OrderDraftsPage'
import type { ReactNode } from 'react'
import { createBrowserRouter, Navigate } from 'react-router-dom'

import AppLayout from './layout/AppLayout'
import LoginPage from '../modules/auth/LoginPage'
import CustomerDetailPage from '../modules/customer/CustomerDetailPage'
import CustomerListPage from '../modules/customer/CustomerListPage'
import DuplicateCasePage from '../modules/customer/DuplicateCasePage'
import KnowledgePage from '../modules/knowledge/KnowledgePage'
import DocumentsPage from '../modules/contract/DocumentsPage'
import CasesPage from '../modules/cases/CasesPage'
import ProductInsightsPage from '../modules/product/ProductInsightsPage'
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
import WeComPage from '../modules/wecom/WeComPage'
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
