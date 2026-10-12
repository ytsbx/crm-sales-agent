import { useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'

import { fetchMe } from '../api/auth'
import { currentAuthEpoch, useAuthStore } from '../store/auth'

/**
 * 让本地那份「用户 + 权限」**跟着服务器走**（主人 2026-10-12 拍板）。
 *
 * ## 要解决的问题
 *
 * 权限原本只在**登录那一刻**抄一次（`LoginPage` 调 `fetchMe`），之后写进持久化的
 * zustand store 就不再变。于是管理员改了权限，**在线的人刷新页面也没用** ——
 * 刷新只是照着本地那份旧清单重画一遍菜单，而整个前端唯一"重新抄"的时机是重新登录。
 * 后端其实一直是对的（每个请求都现查权限），缺的只是界面上看得见的入口。
 *
 * ## 三条设计（都有取舍，别顺手改）
 *
 * 1. **不阻塞渲染**：本地那份是持久化的（zustand v5 默认**同步**恢复），所以
 *    首屏本来就有 user 可以画菜单。这里只做"后台拉一次、回来就覆盖"，
 *    **不等网络**。若改成"等结果回来再画"，首屏会变慢，而且没有任何收益。
 *
 * 2. **60 秒轮询**（`refetchInterval`）：`NotificationBell` 早就这么干了，
 *    同一个项目里同一种节奏。这样权限变更**不用刷新也能生效**（近实时），
 *    代价只是每分钟一个轻量请求。只做"启动拉一次"也能交差，但既然现成的机制
 *    就在旁边，没必要留那个缺口。
 *
 * 3. **写回前比「登录代」**（`currentAuthEpoch`）：这是个"异步回来写登录态"的流程，
 *    而 store 的注释专门警告过这类流程 —— 「退出前发出、退出后才回来」的响应
 *    如果直接写回，界面会"自己又登回去了"。做法是**发请求时记下代号、回来时比**，
 *    对不上就丢弃这次结果。
 *
 * ## 失败怎么办（必须设计的降级）
 *
 * - 401 / 会话已作废：**不在**这里处理 —— `api/client.ts` 的拦截器已经
 *   清本地并跳登录页了，那是唯一该管这件事的地方。
 * - 其他失败（网络抖、后端重启）：**静默沿用本地那份**，照常用。
 *   绝不能因为"权限没拉回来"就把人挡在门外 —— 后端每次请求都会拦，
 *   本地旧权限最多是多显示一个入口。
 */
export function useCurrentUserSync(): void {
  const token = useAuthStore((state) => state.token)
  const setUser = useAuthStore((state) => state.setUser)
  /**
   * 这次请求**出发时**的登录代。
   *
   * ⚠️ 必须用 ref 在**取数开始时**写入。第一版我写成模块级 `let epochAtStart =
   * currentAuthEpoch()` —— 那个只在模块导入时求值一次，之后永远等于它自己，
   * 比较恒成立，等于没判。**"记下出发时的状态"必须在出发那一刻做。**
   */
  const epochAtFetch = useRef(currentAuthEpoch())

  const query = useQuery({
    queryKey: ['auth-me', token],
    queryFn: async () => {
      epochAtFetch.current = currentAuthEpoch()
      return fetchMe()
    },
    enabled: Boolean(token),
    // 见上面第 2 条：权限变更要能"不用刷新就生效"
    refetchInterval: 60_000,
    // 窗口重新聚焦时也拉一次：用户切回来看到的就是最新的
    refetchOnWindowFocus: true,
    // 失败不清空、也不无限重试：沿用本地那份就是了
    retry: false,
  })

  useEffect(() => {
    if (!query.data) return
    // 见上面第 3 条：这次结果出发之后，用户可能已经退出 / 换号了
    if (currentAuthEpoch() !== epochAtFetch.current) return
    setUser(query.data)
  }, [query.data, setUser])
}
