/**
 * 新建 SKU 时的"暂存图片"选择器（2026-10-10，主人拍板：新建弹窗里就能选图，保存时一并上传）。
 *
 * 为什么需要"暂存"这一步：**SKU 还不存在**，没有 id 可挂 —— 文件中心的
 * `/files/upload` 与 `/business/sku/{id}/files` 都要一个已存在的 SKU 才能关联。
 * 所以这里只把用户选的 `File` 留在内存里预览，点保存时先建 SKU、拿到 id 再逐张上传。
 *
 * 与编辑态的区别：编辑已有 SKU 时**不显示这个** —— 那时 SKU 有 id，
 * 直接点列表里的图片格（或「图片」列）就能传，不必绕一层暂存。
 */
import { useEffect, useRef, useState } from 'react'
import { Button, Toast } from '@douyinfe/semi-ui'

interface Props {
  /** 已选待上传的文件（受控） */
  files: File[]
  onChange: (files: File[]) => void
  /** 单个文件大小上限（MB），仅前端前置校验；后端还有一道 */
  maxSizeMb?: number
  disabled?: boolean
}

/** 本地预览用的 objectURL：随文件列表变化重建，旧的必须 revoke */
function useObjectUrls(files: File[]): string[] {
  const [urls, setUrls] = useState<string[]>([])
  const ref = useRef<string[]>([])
  useEffect(() => {
    const next = files.map((f) => window.URL.createObjectURL(f))
    ref.current.forEach((u) => window.URL.revokeObjectURL(u))
    ref.current = next
    setUrls(next)
    return () => {
      next.forEach((u) => window.URL.revokeObjectURL(u))
      ref.current = []
    }
  }, [files])
  return urls
}

export default function PendingImagePicker({
  files,
  onChange,
  maxSizeMb = 20,
  disabled = false,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const urls = useObjectUrls(files)

  const addFiles = (picked: FileList | null) => {
    if (!picked || picked.length === 0) return
    const list = Array.from(picked)
    const notImage = list.filter((f) => !f.type.startsWith('image/'))
    if (notImage.length > 0) {
      Toast.warning(`这里只收图片（jpg / png / webp / gif）：${notImage.map((f) => f.name).join('、')}`)
      return
    }
    const tooBig = list.filter((f) => f.size > maxSizeMb * 1024 * 1024)
    if (tooBig.length > 0) {
      Toast.warning(`单张不能超过 ${maxSizeMb} MB：${tooBig.map((f) => f.name).join('、')}`)
      return
    }
    onChange([...files, ...list])
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
          可以先选好图片，**点保存时一并上传**（单张不超过 {maxSizeMb} MB）。
          保存后也能在列表的「图片」列随时补。
        </div>
        <>
          <input
            ref={inputRef}
            type="file"
            accept="image/*"
            multiple
            style={{ display: 'none' }}
            onChange={(event) => {
              addFiles(event.target.files)
              event.target.value = '' // 否则连续选同一个文件不再触发
            }}
          />
          <Button size="small" disabled={disabled} onClick={() => inputRef.current?.click()}>
            选择图片
          </Button>
        </>
      </div>

      {files.length === 0 ? (
        <div
          style={{
            padding: '16px 12px',
            textAlign: 'center',
            color: 'var(--crm-text-3)',
            fontSize: 12,
            border: '1px dashed var(--crm-outline)',
            borderRadius: 6,
          }}
        >
          还没选图片
        </div>
      ) : (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {files.map((file, index) => (
            <div
              key={`${file.name}-${file.size}-${index}`}
              style={{
                width: 84,
                border: '1px solid var(--crm-outline)',
                borderRadius: 6,
                overflow: 'hidden',
              }}
            >
              <div style={{ height: 84, background: 'rgba(0,0,0,0.03)' }}>
                {urls[index] && (
                  <img
                    src={urls[index]}
                    alt={file.name}
                    style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                  />
                )}
              </div>
              <div style={{ padding: '4px 6px', fontSize: 11 }}>
                <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={file.name}>
                  {file.name}
                </div>
                <div
                  role="button"
                  tabIndex={0}
                  style={{ color: 'var(--crm-error)', cursor: 'pointer' }}
                  onClick={() => onChange(files.filter((_, i) => i !== index))}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') onChange(files.filter((_, i) => i !== index))
                  }}
                >
                  移除
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
