import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'

import { useTabsStore } from '../store/tabs'

/**
 * 详情页把页签标题换成真实记录名。
 *
 * 为什么要有：`AppLayout` 只能按路由给一个通用名（“客户详情”“报价详情”），
 * 同时打开两个客户时两个页签长得一模一样，根本分不清。详情页拿到数据后调这里，
 * 页签就变成客户名 / 报价号 / 订单号。
 *
 * `label` 为空（还没加载完 / 无权限）时不改，保留通用标题。
 */
export function useTabLabel(label: string | null | undefined) {
  const { pathname } = useLocation()
  useEffect(() => {
    if (!label || !label.trim()) return
    useTabsStore.getState().updateTabLabel(pathname, label.trim())
  }, [pathname, label])
}
