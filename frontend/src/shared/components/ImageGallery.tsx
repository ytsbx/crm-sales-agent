/**
 * 产品图片墙（方案 §7「产品图片接通」的缩略图形态）。
 *
 * 为什么单独做一个组件，而不是继续用通用的 `AttachmentPanel`：
 * `AttachmentPanel` 是**通用附件**表——什么文件都收（图片/PDF/Excel…），
 * 以"文件名 + 大小"的**列表**呈现。那适合"资料"（图纸、规格书、回款凭证），
 * 但产品图片要的是**一眼看到图**：以图为主、缩略图排开、点开看大图。
 * 两者的信息重心不同，混在一张表里既看不清图、也容易把图片和资料混为一谈。
 *
 * 数据完全复用既有文件中心（`/business/product/{id}/files` + `/files/{id}/preview`），
 * **没有新增后端接口、没有新增表** —— 图片本来就是附件，只是换个看法。
 *
 * 两个实现要点：
 *  1. **缩略图和预览都要走 blob**：`/files/{id}/preview` 需要 Authorization 头，
 *     不能直接塞进 `<img src>`。所以取回 blob 再 `createObjectURL`。
 *  2. **objectURL 必须 revoke**：否则 blob 一直留在内存里，翻几十张图就很明显。
 *     缩略图在卸载/列表变化时统一释放，预览大图单独释放（同 `AttachmentPanel`）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Button, Modal, Popconfirm, Spin, Toast } from '@douyinfe/semi-ui'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  deleteFile,
  fetchFilePreview,
  listBusinessFiles,
  uploadFile,
  type FileRow,
} from '../../shared/api/file'
import { usePermissions } from '../../shared/hooks/permissions'

interface Props {
  /** 业务对象类型，产品固定传 `product` */
  businessType: string
  businessId: number
  /**
   * 上传/删除要**目标模块自己的**写权限码（与后端 `access.py` 的映射一致）。
   * 不传就退回文件中心（`file:manage`），那会与后端口径不一致。
   */
  writePermission?: string
  /** 单个文件大小上限（MB），仅用于文案与前置校验；后端还有一道 */
  maxSizeMb?: number
}

/** 只把图片挑出来：判据是 mime，不是扩展名（扩展名可以随便改） */
function isImage(row: FileRow): boolean {
  return (row.mime_type ?? '').toLowerCase().startsWith('image/')
}

function humanSize(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

export default function ProductImageGallery({
  businessType,
  businessId,
  writePermission = 'product:manage',
  maxSizeMb = 20,
}: Props) {
  const { can } = usePermissions()
  const queryClient = useQueryClient()
  const canWrite = can('file:manage') && can(writePermission)
  const inputRef = useRef<HTMLInputElement>(null)

  //: 缩略图的 objectURL 缓存：fileId -> url。整批放/整批收，避免逐张泄漏。
  const [thumbs, setThumbs] = useState<Record<number, string>>({})
  const [thumbFailed, setThumbFailed] = useState<Record<number, boolean>>({})
  const [uploading, setUploading] = useState(false)
  //: 大图查看：{ 列表里的下标 }，null = 关闭。用下标而不是对象，方便左右翻。
  const [viewerIndex, setViewerIndex] = useState<number | null>(null)
  const [viewerUrl, setViewerUrl] = useState<string | null>(null)
  const [viewerLoading, setViewerLoading] = useState(false)

  const query = useQuery({
    queryKey: ['business-files', businessType, businessId],
    queryFn: () => listBusinessFiles(businessType, businessId),
    enabled: Number.isFinite(businessId),
  })

  const images = useMemo(() => (query.data ?? []).filter(isImage), [query.data])
  const imagesKey = useMemo(() => images.map((r) => r.id).join(','), [images])

  const refresh = useCallback(
    () => queryClient.invalidateQueries({ queryKey: ['business-files', businessType, businessId] }),
    [queryClient, businessType, businessId],
  )

  // 整批取缩略图。用 `cancelled` 挡住"取到一半列表变了"时把旧 url 写回去。
  useEffect(() => {
    let cancelled = false
    const created: string[] = []
    const ids = imagesKey ? imagesKey.split(',').map(Number) : []
    if (ids.length === 0) {
      setThumbs({})
      return
    }
    void (async () => {
      const next: Record<number, string> = {}
      const failed: Record<number, boolean> = {}
      await Promise.all(
        ids.map(async (id) => {
          try {
            const { blob } = await fetchFilePreview(id)
            if (cancelled) return
            const url = window.URL.createObjectURL(blob)
            created.push(url)
            next[id] = url
          } catch {
            failed[id] = true
          }
        }),
      )
      if (cancelled) {
        created.forEach((u) => window.URL.revokeObjectURL(u))
        return
      }
      setThumbs(next)
      setThumbFailed(failed)
    })()
    return () => {
      cancelled = true
      created.forEach((u) => window.URL.revokeObjectURL(u))
    }
  }, [imagesKey])

  // 大图同样走 blob；切换/关闭时释放上一张
  useEffect(() => {
    if (viewerIndex === null) {
      setViewerUrl((old) => {
        if (old) window.URL.revokeObjectURL(old)
        return null
      })
      return
    }
    const target = images[viewerIndex]
    if (!target) return
    let cancelled = false
    setViewerLoading(true)
    void (async () => {
      try {
        const { blob } = await fetchFilePreview(target.id)
        if (cancelled) return
        const url = window.URL.createObjectURL(blob)
        setViewerUrl((old) => {
          if (old) window.URL.revokeObjectURL(old)
          return url
        })
      } catch (error) {
        Toast.error(error instanceof Error ? error.message : '图片加载失败')
      } finally {
        if (!cancelled) setViewerLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [viewerIndex, images])

  const removeMutation = useMutation({
    mutationFn: (fileId: number) => deleteFile(fileId),
    onSuccess: () => {
      Toast.success('图片已删除')
      void refresh()
    },
    onError: (error: unknown) =>
      Toast.error(error instanceof Error ? error.message : '删除失败'),
  })

  const handleFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    const list = Array.from(files)
    const tooBig = list.filter((f) => f.size > maxSizeMb * 1024 * 1024)
    if (tooBig.length > 0) {
      Toast.warning(`单个文件不能超过 ${maxSizeMb} MB：${tooBig.map((f) => f.name).join('、')}`)
      return
    }
    const notImage = list.filter((f) => !f.type.startsWith('image/'))
    if (notImage.length > 0) {
      // 文案说清"这里只收图片"，避免有人把 PDF 传进来以为也能显示
      Toast.warning(`这里只收图片（jpg / png / webp / gif）：${notImage.map((f) => f.name).join('、')}`)
      return
    }
    setUploading(true)
    try {
      for (const file of list) {
        await uploadFile(file, { businessType, businessId })
      }
      Toast.success(`已上传 ${list.length} 张图片`)
      await refresh()
    } catch (error) {
      Toast.error(error instanceof Error ? error.message : '上传失败')
    } finally {
      setUploading(false)
    }
  }

  const step = (delta: number) => {
    if (viewerIndex === null || images.length === 0) return
    setViewerIndex((viewerIndex + delta + images.length) % images.length)
  }

  return (
    <div>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
          marginBottom: 12,
        }}
      >
        <div style={{ color: 'var(--crm-text-3)', fontSize: 12 }}>
          产品图片单独放这里（jpg / png / webp / gif），单张不超过 {maxSizeMb} MB。
          规格书、图纸等资料请放下面的「产品资料与图片」。
        </div>
        {canWrite && (
          <>
            <input
              ref={inputRef}
              type="file"
              accept="image/*"
              multiple
              style={{ display: 'none' }}
              onChange={(event) => {
                void handleFiles(event.target.files)
                // 清空 value：否则连续选同一个文件不会再触发 onChange
                event.target.value = ''
              }}
            />
            <Button
              theme="solid"
              type="primary"
              loading={uploading}
              onClick={() => inputRef.current?.click()}
            >
              上传图片
            </Button>
          </>
        )}
      </div>

      {query.isLoading ? (
        <div style={{ padding: 24, textAlign: 'center' }}>
          <Spin />
        </div>
      ) : images.length === 0 ? (
        <div
          style={{
            padding: '28px 12px',
            textAlign: 'center',
            color: 'var(--crm-text-3)',
            border: '1px dashed var(--crm-outline)',
            borderRadius: 6,
          }}
        >
          还没有产品图片
          {canWrite ? '，点右上角「上传图片」加几张' : ''}
        </div>
      ) : (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
          {images.map((row, index) => (
            <div
              key={row.id}
              style={{
                width: 132,
                border: '1px solid var(--crm-outline)',
                borderRadius: 6,
                overflow: 'hidden',
                background: 'var(--crm-surface-2, transparent)',
              }}
            >
              <div
                role="button"
                tabIndex={0}
                title={`${row.file_name}（${humanSize(row.size)}）`}
                onClick={() => setViewerIndex(index)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') setViewerIndex(index)
                }}
                style={{
                  height: 132,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  cursor: 'zoom-in',
                  background: 'rgba(0,0,0,0.03)',
                }}
              >
                {thumbs[row.id] ? (
                  <img
                    src={thumbs[row.id]}
                    alt={row.file_name}
                    style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                  />
                ) : thumbFailed[row.id] ? (
                  <span style={{ color: 'var(--crm-error)', fontSize: 12 }}>加载失败</span>
                ) : (
                  <Spin />
                )}
              </div>
              <div style={{ padding: '6px 8px', fontSize: 12 }}>
                <div
                  style={{
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                  title={row.file_name}
                >
                  {row.file_name}
                </div>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    color: 'var(--crm-text-3)',
                  }}
                >
                  <span>{humanSize(row.size)}</span>
                  {canWrite && (
                    <Popconfirm
                      title="删除这张图片？"
                      content="删除后不可恢复"
                      onConfirm={() => removeMutation.mutate(row.id)}
                    >
                      <span
                        style={{ color: 'var(--crm-error)', cursor: 'pointer' }}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') removeMutation.mutate(row.id)
                        }}
                      >
                        删除
                      </span>
                    </Popconfirm>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <Modal
        title={
          viewerIndex !== null && images[viewerIndex]
            ? `${images[viewerIndex].file_name}（${viewerIndex + 1}/${images.length}）`
            : '产品图片'
        }
        visible={viewerIndex !== null}
        onCancel={() => setViewerIndex(null)}
        footer={
          images.length > 1 ? (
            <div style={{ display: 'flex', justifyContent: 'center', gap: 8 }}>
              <Button onClick={() => step(-1)}>上一张</Button>
              <Button onClick={() => step(1)}>下一张</Button>
            </div>
          ) : null
        }
        width={920}
        bodyStyle={{ padding: 12 }}
      >
        <div style={{ minHeight: 200, textAlign: 'center' }}>
          {viewerLoading && <Spin />}
          {viewerUrl && !viewerLoading && (
            <img
              src={viewerUrl}
              alt="产品图片"
              style={{ maxWidth: '100%', maxHeight: '68vh', borderRadius: 4 }}
            />
          )}
        </div>
      </Modal>
    </div>
  )
}
