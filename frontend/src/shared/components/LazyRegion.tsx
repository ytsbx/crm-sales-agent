import {
  Component,
  createElement,
  lazy,
  Suspense,
  useMemo,
  useState,
  type ComponentType,
  type ReactNode,
} from 'react'

/**
 * 页面**内部**「按需加载的一小块」：出错时只换掉它自己，不牵连整页。
 *
 * ## 为什么需要（第九批复审 §9.11 第二条）
 *
 * 路由级那个兜底（`app/lazyPage.tsx`）管的是"整个页面自己的代码块没取到"——
 * 那时页面根本还没渲染出来，整页换成提示是对的。但如果**页面已经渲染出来、
 * 用户还填了半张表**，之后某个**局部**按需加载失败，错误会一路冒到路由级兜底，
 * 于是整页被替换、表单被卸载 —— 用户填的东西在点「重新加载」之前就没了。
 *
 * 所以页面里凡是要"点开才去取代码"的区域，都套这个组件：
 *
 * - **只替换自己那一小块**：页面其余部分（包括用户填的内容）留在原处；
 * - **局部重试**：点「重新加载这一块」重新拉一次，不需要整页刷新；
 * - 提示语明说"其他内容和你填的东西都还在"，避免用户以为只能整页刷新。
 *
 * ## 用法
 *
 * ```tsx
 * {showChart && (
 *   <LazyRegion
 *     label="图表"
 *     loader={() => import('./HeavyChart')}
 *     props={{ rows }}
 *   />
 * )}
 * ```
 *
 * ⚠️ `loader` 必须是**稳定的引用**（写成模块顶层函数或 `useCallback`）：
 * 每次渲染换一个新函数会让区域反复重新加载。
 */
export function LazyRegion<P extends object>({
  loader,
  label,
  fallback,
  props,
}: {
  /** 与 `React.lazy` 的 loader 同形：`() => import('./Xxx')`。 */
  loader: () => Promise<{ default: ComponentType<P> }>
  /** 出错提示里说清是"哪一块"没加载出来（人话，例如「图表」「附件预览」）。 */
  label: string
  fallback?: ReactNode
  props?: P
}) {
  const [attempt, setAttempt] = useState(0)
  return (
    // `key={attempt}`：重试 = **把这一块整体重挂一次**（而不是只清一下错误状态）。
    // 必须重挂的原因在下面的 `RegionAttempt` 里说 —— `React.lazy` 失败后会一直
    // 抛缓存里的那个错误，不换新实例就永远重试不起来。
    <RegionAttempt
      key={attempt}
      loader={loader}
      label={label}
      fallback={fallback}
      props={props}
      onRetry={() => setAttempt((n) => n + 1)}
    />
  )
}

function RegionAttempt<P extends object>({
  loader,
  label,
  fallback,
  props,
  onRetry,
}: {
  loader: () => Promise<{ default: ComponentType<P> }>
  label: string
  fallback?: ReactNode
  props?: P
  onRetry: () => void
}) {
  // 这一层被 `key` 重挂时 `useMemo` 会重新算，于是拿到一个**新的** `lazy` 实例、
  // loader 被重新调用一次 —— 这就是"局部重试"能真正重试的机制。
  // `loader` 保持稳定引用（模块顶层函数 / useCallback），所以这里只会因重挂而重算。
  const Region = useMemo(() => lazy(loader), [loader])

  return (
    <RegionBoundary label={label} onRetry={onRetry}>
      <Suspense
        fallback={
          fallback ?? <div className="lazy-region-loading">{label}正在加载…</div>
        }
      >
        {createElement(Region as ComponentType<P>, (props ?? {}) as P)}
      </Suspense>
    </RegionBoundary>
  )
}

/**
 * 只管**自己这一小块**的错误边界（`LazyRegion` 内部用的就是它）。
 *
 * 与路由级那个的关键区别：**不刷新页面**。它只把自己换成
 * 「这一块没加载出来 + 重新加载这一块」的提示，外侧的页面与表单原样保留。
 */
export class RegionBoundary extends Component<
  { children: ReactNode; label: string; onRetry: () => void },
  { failed: boolean }
> {
  state: { failed: boolean } = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <div className="lazy-region-error" role="alert">
        <div>{this.props.label}没能加载出来，可能是系统刚更新过。</div>
        <div className="lazy-region-hint">
          页面其他内容、以及你已经填的东西都还在，不用整页刷新。
        </div>
        <button
          type="button"
          className="semi-button semi-button-primary"
          onClick={this.props.onRetry}
        >
          重新加载这一块
        </button>
      </div>
    )
  }
}
