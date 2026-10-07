import { useEffect, useState, type ComponentType } from 'react'

import { LazyRegion } from '../../shared/components/LazyRegion'
import { lazyPage } from '../lazyPage'

/**
 * 自测页：把「页面已经打开、内容填了一半时，某一块按需加载失败」演一遍。
 *
 * 这个页面**不进主包**（`index.html` 不引用它，`vite build` 也只打包 index），
 * 只有冒烟脚本会用 `agent-browser` 打开它跑真机断言：
 *
 * 1. 挂载 → 填一段"未保存的内容"；
 * 2. 点「让那一块去加载」→ 那一块**故意取不到代码**；
 * 3. 断言：输入框还在、值没丢、页面其余内容没被替换（这一条是核心：
 *    修之前整页会被换成错误提示，表单连值一起消失）；
 * 4. 点「重新加载这一块」→ 只重试那一块、恢复正常；
 * 5. 继续改内容并提交，断言流程正常。
 *
 * 另有一个 `?fail=1` 的分支，验路由级那条老口径不被破坏：
 * **首次**加载失败仍会自动刷新一次，但绝不无限刷新。
 */

/**
 * 那一块"要按需加载"的内容：**第一次故意失败**，重试时真的给出来。
 *
 * 用模块级计数而不是 state：`React.lazy` 的 loader 在渲染期被调用，
 * 在里面 `setState` 会触发"渲染期间更新另一个组件"的告警。
 */
let regionAttempts = 0

async function loadRegion(): Promise<{ default: ComponentType }> {
  regionAttempts += 1
  if (regionAttempts === 1) {
    // 真实世界里这句长这样：发版后旧页面还引用着已经被替换掉的代码块
    throw new Error('Failed to fetch dynamically imported module: /missing-region.js')
  }
  return {
    default: () => <div className="lazy-region-ok">这一块已经加载出来了</div>,
  }
}

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

        {regionVisible && <LazyRegion label="「图表」" loader={loadRegion} />}
      </div>
    </div>
  )
}

export default function LazyRegionSelfTest() {
  const failRoute =
    new URLSearchParams(window.location.search).get('fail') === '1'
  return failRoute ? <FirstLoadFailCase /> : <RegionCase />
}
