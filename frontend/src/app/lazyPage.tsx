import { Component, createElement, lazy, Suspense, type ComponentType, type ReactNode } from 'react'

/**
 * 路由级懒加载的统一包装（第九批 §9.11）。
 *
 * 背景：33 个页面此前全部在 `router.tsx` 顶部**同步 import**，构建出**一个**
 * 1.79 MB（gzip 约 480 KB）的 JS —— 进登录页也要先把报价中心、洞察、知识库
 * 这些页面的代码全下载下来，手机与弱网下首屏很慢。
 *
 * 这里一次做掉三件事：
 * 1. `lazy`：每个页面单独成块，图表、Excel 这类重依赖跟着**用到它的页面**加载，
 *    不进首屏；
 * 2. `Suspense`：统一的轻量加载态；
 * 3. 错误边界：处理"发版后旧页面还在引用已被替换的资源"
 *    （`Failed to fetch dynamically imported module`）—— 这类失败在**首次
 *    加载时自动刷新一次**，把用户带到新版本；用 sessionStorage 留个标记，
 *    避免资源真的有问题时无限刷新。
 *
 * ⚠️ **刻意不做的事**
 * - 不在页面**已经渲染出来之后**自动刷新：那时用户可能已经填了半张表，
 *   刷一下全没了（报告要求"不因资源失败直接丢弃录入"）。渲染之后的错误
 *   只提示 + 给按钮，由用户决定何时刷新。
 * - 不用 localStorage 存表单草稿来"兜底"：客户资料属敏感数据，
 *   未经授权的本地持久化反而制造新的泄露面。
 */

const RELOAD_FLAG = 'crm-chunk-reload'

class PageBoundary extends Component<
  { children: ReactNode },
  { message: string | null }
> {
  state: { message: string | null } = { message: null }

  static getDerivedStateFromError(error: unknown) {
    return { message: error instanceof Error ? error.message : String(error) }
  }

  componentDidCatch(error: unknown) {
    const text = error instanceof Error ? error.message : ''
    const staleChunk = /dynamically imported module|Importing a module script failed|ChunkLoadError/i.test(
      text,
    )
    // 只对"块取不到"自动刷新，且**每会话只刷一次** —— 否则资源真缺失时
    // 会陷入刷新死循环，用户连"重新加载"按钮都点不到。
    if (!staleChunk || sessionStorage.getItem(RELOAD_FLAG)) return
    sessionStorage.setItem(RELOAD_FLAG, '1')
    window.location.reload()
  }

  render() {
    if (this.state.message !== null) {
      return (
        <div className="page-container">
          <div style={{ padding: '48px 0', textAlign: 'center' }}>
            <div style={{ marginBottom: 8 }}>这个页面没能加载出来，可能是系统刚更新过。</div>
            <div
              style={{
                color: 'var(--crm-text-3)',
                fontSize: 12,
                marginBottom: 16,
              }}
            >
              你刚才填的内容没有被提交，重新加载后需要再操作一次。
            </div>
            <button
              type="button"
              className="semi-button semi-button-primary"
              onClick={() => {
                sessionStorage.removeItem(RELOAD_FLAG)
                window.location.reload()
              }}
            >
              重新加载
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

/** 把一个页面组件包成"按需加载 + 加载态 + 出错可重试"的路由元素。 */
export function lazyPage<P extends object>(
  loader: () => Promise<{ default: ComponentType<P> }>,
) {
  const Page = lazy(loader)

  function LazyPage(props: P) {
    return (
      <PageBoundary>
        <Suspense fallback={<div className="page-container">页面加载中…</div>}>
          {createElement(Page, props)}
        </Suspense>
      </PageBoundary>
    )
  }

  return LazyPage
}
