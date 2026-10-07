import * as React from 'react'
import { useEffect, useState, type ComponentType } from 'react'

import { LazyRegion } from '../../shared/components/LazyRegion'
import { lazyPage } from '../lazyPage'

/**
 * 自测页：把「页面已经打开、内容填了一半时，某一块按需加载失败」演一遍。
 *
 * 这个页面**不进主包**（`index.html` 不引用它，`vite build` 也只打包 index），
 * 只有冒烟脚本会用浏览器打开它跑真机断言。
 *
 * ## 两种模式
 *
 * **① 局部失败兜底（默认）**：挂载 → 填一段"未保存的内容" → 点「让那一块去加载」
 * → 那一块取不到代码 → 断言输入框还在、值没丢、页面其余内容没被替换
 * （这条是核心：修之前整页会被换成错误提示，表单连值一起消失）→
 * 点「重新加载这一块」→ 只重试那一块、恢复正常 → 继续改内容并提交。
 *
 * **② 真实网络失败（`?region=<模块地址>`）**：那一块改成**真的去取一个模块**，
 * 地址由冒烟脚本临时起的小服务提供。这是本轮复核补的那条 ——
 * 只验"重挂 React 组件能成功"是不够的：浏览器会把失败的模块地址记在**模块映射**
 * 里，同一个地址再取**连请求都不发**。脚本靠服务端计数断言"重试**确实发出了新的请求**"，
 * 并且表单内容仍在。
 *   - `?region=<地址>` 服务先 503、随后恢复 200 → 点重试必须成功；
 *   - 地址一直 404 → 界面应当改口成「刷新页面」，不能给一个永远点不通的重试。
 *
 * 另有一个 `?fail=1` 的分支，验路由级那条老口径不被破坏：
 * **首次**加载失败仍会自动刷新一次，但绝不无限刷新。
 */

// 被加载的那个"外部模块"不在打包范围内、拿不到 `react` 这个包名，
// 所以把页面用的这一份 React 挂到 window 上给它用（同一个实例，元素才认得）。
const win = window as unknown as Record<string, unknown>
win.__React = React

/** `?region=<模块地址>`：有它就演"真实网络失败"，没有就走下面的本地版。 */
const REGION_URL = new URLSearchParams(window.location.search).get('region')

/** 真实网络加载：地址是运行时传进来的，不能让打包器静态分析。 */
function networkLoader(url: string) {
  return () => import(/* @vite-ignore */ url) as Promise<{ default: ComponentType }>
}

/**
 * 本地版：**第一次故意失败**，重试时真的给出来。
 *
 * 用模块级计数而不是 state：`React.lazy` 的 loader 在渲染期被调用，
 * 在里面 `setState` 会触发"渲染期间更新另一个组件"的告警。
 */
let regionAttempts = 0

async function loadRegionLocal(): Promise<{ default: ComponentType }> {
  regionAttempts += 1
  if (regionAttempts === 1) {
    // 这条报错**刻意不带模块地址**：它演的是"loader 自己第一次拿不到"，
    // 而不是"某个真实地址被浏览器记住了"。带上地址会被 `LazyRegion` 当成
    // 真实网络加载、去换个地址重取，这条用例就测偏了（而且本地版本来也没有地址）。
    throw new Error('自测：这一块的代码第一次故意取不到')
  }
  return {
    default: () => <div className="lazy-region-ok">这一块已经加载出来了</div>,
  }
}

const regionLoader = REGION_URL ? networkLoader(REGION_URL) : loadRegionLocal

/** 必定取不到代码的整页：用来验「首次加载失败只自动刷新一次」。 */
const AlwaysFailPage = lazyPage<Record<string, never>>(async () => {
  throw new Error('Failed to fetch dynamically imported module: /missing-page.js')
})

function FirstLoadFailCase() {
  return (
    <div className="page-container">
      <AlwaysFailPage />
    </div>
  )
}

function RegionCase() {
  const [name, setName] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [regionVisible, setRegionVisible] = useState(false)

  // 每次挂载都重置"第一次必失败"，脚本可以反复跑同一个页面
  useEffect(() => {
    regionAttempts = 0
  }, [])

  return (
    <div className="page-container">
      <h2 className="page-title">自测：按需加载区域的局部兜底</h2>
      <p className="page-subtitle">
        验证「页面已经打开、内容填了一半时，某一块按需加载失败不能把整页顶掉」。
      </p>

      <div style={{ display: 'grid', gap: 14, maxWidth: 560 }}>
        <label style={{ display: 'grid', gap: 6 }}>
          <span>未保存的内容</span>
          <input
            id="selftest-name"
            className="semi-input"
            value={name}
            placeholder="在这里填点东西"
            onChange={(event) => setName(event.target.value)}
          />
        </label>

        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <button
            type="button"
            className="semi-button semi-button-primary"
            onClick={() => setSubmitted(name)}
          >
            提交
          </button>
          {submitted !== '' && <span id="selftest-submitted">已提交：{submitted}</span>}
        </div>

        <div>
          <button
            type="button"
            className="semi-button"
            onClick={() => setRegionVisible(true)}
          >
            让那一块去加载
          </button>
        </div>

        {regionVisible && <LazyRegion label="「图表」" loader={regionLoader} />}
      </div>
    </div>
  )
}

export default function LazyRegionSelfTest() {
  const failRoute =
    new URLSearchParams(window.location.search).get('fail') === '1'
  return failRoute ? <FirstLoadFailCase /> : <RegionCase />
}
