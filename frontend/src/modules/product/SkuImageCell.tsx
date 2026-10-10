/**
 * SKU 列表里的「图片」单元格（2026-10-10，主人口径：**图片就放 SKU 列表这儿**）。
 *
 * 只做一件事：在这一行显示该 SKU 的第一张图（小缩略图）+ 图片张数；点一下打开
 * 图片墙弹窗（`ImageGallery`）看全部、上传、删除。
 *
 * 为什么缩略图也要走 blob：`/files/{id}/preview` 需要 Authorization 头，
 * 不能直接塞进 `<img src>`。取回 blob 再 `createObjectURL`，并在**换图/卸载时 revoke**，
 * 否则翻页几次 blob 就一直留在内存里。
 *
 * 数据由父组件用**批量接口**一次取好、按 sku_id 传进来 —— 每行各查一次就是 N+1。
 */
import { useEffect, useRef, useState } from 'react'
import { Spin } from '@douyinfe/semi-ui'
import { fetchFilePreview } from '../../shared/api/file'

interface Props {
  /** 该 SKU 的图片附件（已由父组件批量取好并过滤成图片） */
  images: { id: number }[]
  /** 无图且可写时，显示一个"+"提示可以加图 */
  canWrite?: boolean
  /** 点击回调：打开图片墙弹窗 */
  onClick: () => void
  /** 缩略图边长，默认 40 */
  size?: number
}

export default function SkuImageCell({ images, canWrite = false, onClick, size = 40 }: Props) {
  const first = images[0]
  const [url, setUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  // 记录当前 url，卸载时 revoke（用 ref 是为了在 cleanup 里拿到最新值）
  const urlRef = useRef<string | null>(null)

  useEffect(() => {
    let cancelled = false
    if (!first) {
      setUrl((old) => {
        if (old) window.URL.revokeObjectURL(old)
        return null
      })
      urlRef.current = null
      return
    }
    setFailed(false)
    void (async () => {
      try {
        const { blob } = await fetchFilePreview(first.id)
        if (cancelled) return
        const next = window.URL.createObjectURL(blob)
        setUrl((old) => {
          if (old) window.URL.revokeObjectURL(old)
          return next
        })
        urlRef.current = next
      } catch {
        if (!cancelled) setFailed(true)
      }
    })()
    return () => {
      cancelled = true
    }
    // ⚠️ 依赖里必须带上**张数**（2026-10-10 修）：只看 `first.id` 的话，
    //    "给已有图的 SKU 再加一张"不会改变第一张的 id（新图排在最前时才会变），
    //    于是缩略图不重取，表现为"传完图要手动刷浏览器"。
  }, [first?.id, images.length, first])

  // 卸载时把最后一个 url 也收掉
  useEffect(
    () => () => {
      if (urlRef.current) window.URL.revokeObjectURL(urlRef.current)
    },
    [],
  )

  const box: React.CSSProperties = {
    width: size,
    height: size,
    borderRadius: 4,
    border: '1px solid var(--crm-outline)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    cursor: 'pointer',
    overflow: 'hidden',
    background: 'rgba(0,0,0,0.03)',
    fontSize: 11,
    color: 'var(--crm-text-3)',
  }

  return (
    <div
      role="button"
      tabIndex={0}
      title={first ? `查看 / 管理图片（共 ${images.length} 张）` : canWrite ? '添加图片' : '暂无图片'}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') onClick()
      }}
      style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
    >
      <div style={box}>
        {url ? (
          <img src={url} alt="SKU 图片" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
        ) : failed ? (
          <span style={{ color: 'var(--crm-error)' }}>!</span>
        ) : first ? (
          <Spin size="small" />
        ) : canWrite ? (
          <span style={{ fontSize: 16, lineHeight: 1 }}>+</span>
        ) : (
          <span>—</span>
        )}
      </div>
      {images.length > 1 && <span style={{ fontSize: 11, color: 'var(--crm-text-3)' }}>{images.length}</span>}
    </div>
  )
}
