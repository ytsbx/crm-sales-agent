import {
  Component,
  createElement,
  useCallback,
  useEffect,
  useRef,
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
 * - **局部重试**：点「重新加载这一块」真的**重新去取一次代码**，不用整页刷新；
 * - 提示语明说"其他内容和你填的东西都还在"，避免用户以为只能整页刷新。
 *
 * ## 重试为什么必须**换一个地址**（本次复核实测）
 *
 * 只把 `React.lazy` 实例重建一次是不够的。浏览器会把"这个地址取失败"记在
 * **模块映射**里，同一地址再 `import()` 一次**连网络请求都不会发**。
 * 实测（本地起一个先 503、后 200 的小服务）：
 *
 * ```text
 * import('/mod.js')            → FAIL   （服务端收到 1 次请求，返回 503）
 * import('/mod.js')  再试一次  → FAIL   （服务端**一次请求都没收到**）
 * import('/mod.js?n=1')        → OK     （换地址 → 新请求 → 200）
 * ```
 *
 * 所以这里的重试是：从报错信息里取出**那次真实请求的地址**，追加一个
 * `__lazy_retry=N` 参数后再取 —— 换成一个新地址，浏览器才肯重新发请求。
 * 取不到地址时（不是网络加载、或浏览器没在报错里带地址）退化为"重跑 loader"，
 * 对自测里那种纯函数式 loader 仍然有效。
 *
 * ⚠️ 别把 `import()` 交给打包器静态分析：地址是运行时才拿到的，
 * 所以那句必须带 `@vite-ignore`，否则打包器会按自己的规则重写、
 * 把 `__lazy_retry` 这个关键参数弄丢。
 *
 * ## 两种失败要分开说（避免给一个永远点不通的重试）
 *
 * - **暂时不通**（网络抖动 / 服务刚恢复）→ 给「重新加载这一块」；
 * - **彻底没了**（发版后旧代码块已被删掉，探测到 404/410）→ 不再给重试，
 *   直接说明"需要刷新页面才能拿到新版本"，并提醒未提交内容会丢。
 *   录入内容一直保留在页面上，由用户自己决定什么时候刷。
 * - 取不到地址、又连着失败两次 → 也改口给「刷新页面」，不给点不完的重试。
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

/** 浏览器在"动态模块取不到"时给的报错里，地址长什么样（Chrome/Edge/Firefox）。 */
const MODULE_URL_PATTERNS = [
  /Failed to fetch dynamically imported module:?\s*(\S+)/i,
  /error loading dynamically imported module:?\s*(\S+)/i,
]

/** 从报错里抠出那次真实请求的地址；抠不到返回 null（Safari 就不给地址）。 */
function moduleUrlFromError(error: unknown): string | null {
  const text = error instanceof Error ? error.message : String(error ?? '')
  for (const pattern of MODULE_URL_PATTERNS) {
    const matched = text.match(pattern)
    // 结尾可能粘着右括号/标点，去掉才是可用的地址
    if (matched) return matched[1].replace(/[)\].,;]+$/, '')
  }
  return null
}

/** 给地址追加一个"换地址"用的参数：同地址的失败被浏览器记住了，换一个才肯重发。 */
function withRetryNonce(url: string, nonce: number): string {
  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}__lazy_retry=${nonce}`
}

async function fetchModule<P>(
  url: string,
  nonce: number,
): Promise<{ default: ComponentType<P> }> {
  const target = withRetryNonce(url, nonce)
  return (await import(/* @vite-ignore */ target)) as { default: ComponentType<P> }
}

/**
 * 探测"这个地址是不是已经彻底没有了"。
 *
 * 只把 **404 / 410** 当作"没了"：探测本身失败（断网、跨域被拦）说明不了问题，
 * 不能因此就断定"旧版本已经不存在"。
 */
async function moduleIsGone(url: string): Promise<boolean> {
  try {
    const response = await fetch(url, { method: 'HEAD', cache: 'no-store' })
    return response.status === 404 || response.status === 410
  } catch {
    return false
  }
}

type Phase = 'loading' | 'ready' | 'failed'

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
  // 已经拿到的**真实地址**（从报错里抠出来的），重试靠它换地址重新取
  const urlRef = useRef<string | null>(null)
  // 每重试一次 +1：既是"换地址"用的参数，也是重新跑 effect 的开关
  const nonceRef = useRef(0)
  // 连着失败了几次：够多次就改口让用户刷新，不给点不完的重试
  const failuresRef = useRef(0)

  const [attempt, setAttempt] = useState(0)
  const [phase, setPhase] = useState<Phase>('loading')
  const [Loaded, setLoaded] = useState<ComponentType<P> | null>(null)
  const [gone, setGone] = useState(false)
  const [exhausted, setExhausted] = useState(false)
  const [note, setNote] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    const run = async () => {
      // "这次是不是带着真实地址去取的"——决定失败后能不能退回重跑 loader。
      // ⚠️ 首次加载（还没有地址）**不能**回退重跑：那等于同一个 loader 被调两次，
      // 自测页那种"第一次必失败"的 loader 会被这一步直接绕过，问题就验不出来了。
      const startedWithUrl = urlRef.current

      try {
        const mod = startedWithUrl
          ? await fetchModule<P>(startedWithUrl, nonceRef.current)
          : await loader()
        if (cancelled) return
        failuresRef.current = 0
        setLoaded(() => mod.default)
        setPhase('ready')
        return
      } catch (error) {
        if (cancelled) return
        if (!urlRef.current) urlRef.current = moduleUrlFromError(error)
      }

      // 换地址还取不到：也可能这个 loader 压根不是网络加载 → 退回重跑 loader。
      if (startedWithUrl && urlRef.current) {
        try {
          const mod = await loader()
          if (cancelled) return
          failuresRef.current = 0
          setLoaded(() => mod.default)
          setPhase('ready')
          return
        } catch {
          /* 还是不行，往下判定到底是"暂时不通"还是"彻底没了" */
        }
      }

      if (cancelled) return
      failuresRef.current += 1
      const target = urlRef.current
      const isGone = target ? await moduleIsGone(target) : false
      if (cancelled) return

      setGone(isGone)
      setExhausted(failuresRef.current >= 3)
      setNote(
        !isGone && failuresRef.current >= 2
          ? '试着又取了一次还是没成。如果系统刚更新过，刷新页面才能拿到新的代码（未提交的内容会丢）。'
          : null,
      )
      setPhase('failed')
    }

    void run()
    return () => {
      cancelled = true
    }
  }, [attempt, loader])

  const retry = useCallback(() => {
    // 状态在这里（事件回调里）复位，而不是在 effect 里同步 setState ——
    // 后者会额外触发一轮渲染，也会让"是不是在加载中"这件事有两个来源。
    setPhase('loading')
    setNote(null)
    nonceRef.current += 1
    setAttempt((n) => n + 1)
  }, [])

  const reload = useCallback(() => window.location.reload(), [])

  /**
   * 加载到的组件**自己在渲染里抛了**也得只换掉这一块（否则又会冒到路由级、
   * 把整页连同表单一起卸载）。这里不自动重试 —— 每次都由用户点按钮触发，
   * 免得"必然抛错"的组件把界面拖进死循环。
   */
  const handleRenderError = useCallback(() => {
    failuresRef.current += 1
    setGone(false)
    setExhausted(failuresRef.current >= 3)
    setNote('这一块内容自己在渲染时出错了，可以点「重新加载这一块」重挂一次。')
    setPhase('failed')
  }, [])

  if (phase === 'loading') {
    return (
      <>{fallback ?? <div className="lazy-region-loading">{label}正在加载…</div>}</>
    )
  }

  if (phase === 'failed') {
    return (
      <RegionErrorUI
        label={label}
        gone={gone}
        exhausted={exhausted}
        note={note}
        onRetry={retry}
        onReload={reload}
      />
    )
  }

  return (
    <RegionBoundary label={label} onError={handleRenderError}>
      {Loaded ? createElement(Loaded, (props ?? {}) as P) : null}
    </RegionBoundary>
  )
}

/** 局部失败的提示：说清"只坏了这一块"，并把「能不能救回来」讲明白。 */
function RegionErrorUI({
  label,
  gone,
  exhausted,
  note,
  onRetry,
  onReload,
}: {
  label: string
  gone: boolean
  exhausted: boolean
  note: string | null
  onRetry: () => void
  onReload: () => void
}) {
  // 彻底没了 / 试够了 → 只给"刷新页面"；否则给"重新加载这一块"
  const suggestReload = gone || exhausted
  return (
    <div className="lazy-region-error" role="alert">
      <div>{gone ? `${label}在当前版本里已经没有了。` : `${label}没能加载出来。`}</div>
      <div className="lazy-region-hint">
        {gone
          ? '这块内容的代码已经被新版本替换掉了，点重试不会成功 —— 需要刷新页面才能拿到新版本。页面其他内容、以及你已经填的东西都还在。'
          : suggestReload
            ? '连着取了几次都没成。刷新页面能拿到最新的代码，但你没提交的内容会丢。页面其他内容、以及你已经填的东西都还在。'
            : '可能是网络暂时不通，或者系统刚更新过。页面其他内容、以及你已经填的东西都还在，不用整页刷新。'}
      </div>
      {note && !suggestReload && <div className="lazy-region-hint">{note}</div>}
      <div>
        {suggestReload ? (
          <button
            type="button"
            className="semi-button semi-button-primary"
            onClick={onReload}
          >
            刷新页面
          </button>
        ) : (
          <button
            type="button"
            className="semi-button semi-button-primary"
            onClick={onRetry}
          >
            重新加载这一块
          </button>
        )}
      </div>
    </div>
  )
}

/**
 * 只管**自己这一小块**的错误边界（`LazyRegion` 内部用的就是它）。
 *
 * 与路由级那个的关键区别：**不刷新页面**。它只把自己换成局部提示，
 * 外侧的页面与表单原样保留。
 */
export class RegionBoundary extends Component<
  { children: ReactNode; label: string; onError: () => void },
  { failed: boolean }
> {
  state: { failed: boolean } = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch() {
    // 交给外面切到"局部失败"提示（外面点重试时会整块重挂，这里的状态自然重置）
    this.props.onError()
  }

  render() {
    return this.state.failed ? null : this.props.children
  }
}
