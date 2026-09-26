import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Modal, Popconfirm, Table, Tag, Toast } from '@douyinfe/semi-ui'

import {
  deleteFile,
  downloadFile,
  fetchFilePreview,
  listBusinessFiles,
  uploadFile,
  type FileRow,
} from '../../shared/api/file'
import { usePermissions } from '../../shared/hooks/permissions'

const SIZE_UNIT = ['B', 'KB', 'MB', 'GB']

function formatSize(size: number): string {
  let value = size
  let unit = 0
  while (value >= 1024 && unit < SIZE_UNIT.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${SIZE_UNIT[unit]}`
}

interface Props {
  businessType: string
  businessId: number
  category?: string
}

export default function AttachmentPanel({ businessType, businessId, category }: Props) {
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const inputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)

  // 预览状态：objectUrl 用完必须 revoke，否则 blob 会一直留在内存里
  const [preview, setPreview] = useState<{ name: string; mime: string; url: string } | null>(null)
  const [previewing, setPreviewing] = useState(false)

  useEffect(
    () => () => {
      if (preview) window.URL.revokeObjectURL(preview.url)
    },
    [preview],
  )

  const openPreview = async (record: FileRow) => {
    setPreviewing(true)
    try {
      const { blob, mime } = await fetchFilePreview(record.id)
      setPreview({ name: record.file_name, mime, url: window.URL.createObjectURL(blob) })
    } catch (error) {
      Toast.error(error instanceof Error ? error.message : '预览失败')
    } finally {
      setPreviewing(false)
    }
  }

  const query = useQuery({
    queryKey: ['business-files', businessType, businessId],
    queryFn: () => listBusinessFiles(businessType, businessId),
    enabled: Number.isFinite(businessId),
  })

  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ['business-files', businessType, businessId] })

  const deleteMutation = useMutation({
    mutationFn: (fileId: number) => deleteFile(fileId),
    onSuccess: () => {
      Toast.success('附件已删除')
      void refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const handleUpload = async (file: File) => {
    setUploading(true)
    try {
      await uploadFile(file, { businessType, businessId, category })
      Toast.success(`${file.name} 上传成功`)
      void refresh()
    } catch (error) {
      Toast.error(error instanceof Error ? error.message : '上传失败')
    } finally {
      setUploading(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  return (
    <>
      <div className="toolbar">
        <div style={{ color: 'var(--crm-text-3)', fontSize: 13 }}>
          合同、图纸、回款凭证都可以放在这里，单个文件不超过 20 MB
        </div>
        <div style={{ flex: 1 }} />
        {can('file:manage') && (
          <>
            <input
              ref={inputRef}
              type="file"
              style={{ display: 'none' }}
              onChange={(event) => {
                const file = event.target.files?.[0]
                if (file) void handleUpload(file)
              }}
            />
            <Button
              theme="solid"
              loading={uploading}
              onClick={() => inputRef.current?.click()}
            >
              上传附件
            </Button>
          </>
        )}
      </div>
      <Table<FileRow>
        columns={[
          {
            title: '文件名',
            dataIndex: 'file_name',
            render: (name: string, record: FileRow) => (
              <a
                style={{ color: 'var(--crm-primary)' }}
                onClick={() =>
                  record.previewable ? void openPreview(record) : downloadFile(record.id, name)
                }
              >
                {name}
              </a>
            ),
          },
          {
            title: '大小',
            dataIndex: 'size',
            width: 110,
            render: (size: number) => formatSize(size),
          },
          { title: '分类', dataIndex: 'category', width: 110, render: (v: string | null) => v ?? '-' },
          { title: '上传人', dataIndex: 'uploader_name', width: 110, render: (v: string | null) => v ?? '-' },
          {
            title: '上传时间',
            dataIndex: 'created_at',
            width: 190,
            render: (v: string) => new Date(v).toLocaleString('zh-CN'),
          },
          {
            title: '存储',
            dataIndex: 'storage_provider',
            width: 100,
            render: (v: string) => <Tag>{v === 'local' ? '本地' : v}</Tag>,
          },
          {
            title: '操作',
            width: 170,
            render: (_: unknown, record: FileRow) => (
              <div style={{ display: 'flex', gap: 10 }}>
                {record.previewable && (
                  <a style={{ color: 'var(--crm-primary)' }} onClick={() => void openPreview(record)}>
                    预览
                  </a>
                )}
                <a
                  style={{ color: 'var(--crm-primary)' }}
                  onClick={() => downloadFile(record.id, record.file_name)}
                >
                  下载
                </a>
                {can('file:manage') && (
                  <Popconfirm
                    title="删除后不可恢复，确认？"
                    onConfirm={() => deleteMutation.mutate(record.id)}
                  >
                    <a style={{ color: 'var(--crm-error)' }}>删除</a>
                  </Popconfirm>
                )}
              </div>
            ),
          },
        ]}
        dataSource={query.data ?? []}
        loading={query.isLoading}
        rowKey="id"
        pagination={false}
        empty="还没有附件"
      />

      {/* 预览弹窗：图片用 img，PDF / 文本用 iframe（浏览器原生渲染） */}
      <Modal
        title={preview ? `预览：${preview.name}` : '预览'}
        visible={preview !== null}
        onCancel={() => setPreview(null)}
        footer={null}
        width={880}
        bodyStyle={{ padding: 12 }}
      >
        {preview && (
          <div style={{ maxHeight: '70vh', overflow: 'auto', textAlign: 'center' }}>
            {preview.mime.startsWith('image/') ? (
              <img
                src={preview.url}
                alt={preview.name}
                style={{ maxWidth: '100%', borderRadius: 4 }}
              />
            ) : (
              <iframe
                src={preview.url}
                title={preview.name}
                style={{ width: '100%', height: '68vh', border: '1px solid var(--crm-outline)' }}
              />
            )}
          </div>
        )}
        <div style={{ marginTop: 8, color: 'var(--crm-text-3)', fontSize: 12 }}>
          预览在浏览器内进行；如需存档请用「下载」。当前会话 {previewing ? '正在加载…' : '已就绪'}。
        </div>
      </Modal>
    </>
  )
}
