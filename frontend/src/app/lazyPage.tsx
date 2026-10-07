import { Component, createElement, lazy, Suspense, useEffect, useRef, type ComponentType, type ReactNode } from 'react'

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
 *   §9.11 复审发现：这条口径原来**只写在注释里、实现里没判断** —— 于是
 *   "先渲染成功、之后某个懒加载块失败"照样整页刷新。现在用 `loadedRef`
 *   把它真正落实了。
 * - 不用 localStorage 存表单草稿来"兜底"：客户资料属敏感数据，
 *   未经授权的本地持久化反而制造新的泄露面。
 */

const RELOAD_FLAG_PREFIX = 'crm-chunk-reload:'

/** 刷新标记按**当前路由**记：每个页面各留一次自动刷新机会。 */
function reloadFlagKey(): string {
  return RELOAD_FLAG_PREFIX + window.location.pathname
}

/** 这个页面在本次会话里是不是已经自动刷过一次了。 */
function alreadyReloaded(): boolean {
  try {
    return sessionStorage.getItem(reloadFlagKey()) === '1'
  } catch {
    // 隐私模式下 sessionStorage 可能直接抛错；那就当作"已经刷过"——
    // 宁可不刷，也不要因为"读不到标记"而反复刷新。
    return true
  }
}

function markReloaded(): void {
  try {
    sessionStorage.setItem(reloadFlagKey(), '1')
  } catch {
    /* 存不下就算了：下面的"已渲染"信号仍能挡住重复刷新 */
  }
}

class PageBoundary extends Component<
  { children: ReactNode; loadedRef: { current: boolean } },
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
    if (!staleChunk) return

    // ⚠️ §9.11 复审：**页面一旦成功渲染过，就绝不自动刷新**。
    // 原来只判"文案匹配 + 本会话没刷过"，于是"先正常渲染、之后某个懒加载块失败"
    // 也会整页刷新 —— 用户填了半张表就这么没了，跟上面写的口径正好相反。
    if (this.props.loadedRef.current) return

    // 防无限刷新：**每个页面各一次**。原来是一个全站共用的开关，
    // 结果任一页面刷过之后，别的页面首屏真的加载失败也不会再自动刷新了。
    if (alreadyReloaded()) return
    markReloaded()
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
                // 手动刷新：上面已提示过"未提交的内容会丢"，由用户自己决定；
                // 顺手清掉本页标记，让下次真遇到旧 chunk 时还能自动救一次。
                try {
                  sessionStorage.removeItem(reloadFlagKey())
                } catch {
                  /* 清不掉也无妨 */
                }
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

/**
 * 子组件**真正挂载**（= 懒加载的块拿到了、页面渲染出来了）时置位。
 *
 * 为什么要在 Suspense **里面**再包一层：直接放进 `LazyPage` 的话，块还在下载、
 * 界面显示"页面加载中…"时那个 effect 就已经跑了 —— 那一步并不代表页面渲染成功。
 * 包在被加载组件的同一层，`lazy` 抛 promise 时这一层根本不会挂载，语义才对。
 */
function MarkLoaded({
  loadedRef,
  children,
}: {
  loadedRef: { current: boolean }
  children: ReactNode
}) {
  useEffect(() => {
    loadedRef.current = true
  }, [loadedRef])
  return <>{children}</>
}

/** 把一个页面组件包成"按需加载 + 加载态 + 出错可重试"的路由元素。 */
export function lazyPage<P extends object>(
  loader: () => Promise<{ default: ComponentType<P> }>,
) {
  const Page = lazy(loader)

  function LazyPage(props: P) {
    // "这个页面成功渲染过没有"——用 ref 不用 state：只给错误边界读，不需要重渲染
    const loadedRef = useRef(false)
    return (
      <PageBoundary loadedRef={loadedRef}>
        <Suspense fallback={<div className="page-container">页面加载中…</div>}>
          <MarkLoaded loadedRef={loadedRef}>{createElement(Page, props)}</MarkLoaded>
        </Suspense>
      </PageBoundary>
    )
  }

  return LazyPage
}
