import { createElement } from 'react'
import { createRoot } from 'react-dom/client'

import '../../index.css'
import LazyRegionSelfTest from './LazyRegionSelfTest'
import { lazyPage } from '../lazyPage'

// 自测页的入口（见 frontend/selftest-lazy-region.html）。
//
// **页面本身也走 `lazyPage`** —— 就是复审方复现那条问题时的做法：路由级兜底包着
// 整个页面、页面里再嵌一块按需加载。这样"局部那块失败"到底会不会把整页顶掉，
// 验的就是真实的那条链路（而不是我另造一个简化场景）。
//
// 刻意**不套 StrictMode**：React 18 的开发态双挂载会把"第一次必失败"的
// 计数跑成两次，自测结果就不确定了。
const Page = lazyPage(async () => ({ default: LazyRegionSelfTest }))

const container = document.getElementById('selftest-root')
if (container) {
  createRoot(container).render(createElement(Page))
}
