import { Spin } from '@douyinfe/semi-ui'

/**
 * 详情页的加载态。
 *
 * 从前各详情页都是 `<div className="page-container">加载中…</div>` —— 一行灰字挂在
 * 整页白底上，网慢的时候看着像页面卡死（2026-11-20 巡检）。统一成"转圈 + 一行字"，
 * 位置在页面垂直 80px 处，不至于一下跳到底部。
 */
export default function PageLoading({ text = '加载中…' }: { text?: string }) {
  return (
    <div className="page-container">
      <div className="page-loading">
        <Spin size="middle" />
        <span>{text}</span>
      </div>
    </div>
  )
}
