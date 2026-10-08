import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Modal, Popconfirm, Table, Tag, Toast } from '@douyinfe/semi-ui'

import {
  deleteFile,
  downloadFile,
  fetchFilePreview,
  listBusinessFiles,
  renameFile,
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
  /**
   * 要不要显示「分类」这一列，**默认不显示**。
   *
   * 为什么默认关：这个场景"有没有分类可填"决定它该不该出现。产品、商机、客户、
   * 跟进这四处上传时**没有任何一步让用户选分类**，所以那一列过去永远是"-"，
   * 看着像"漏填了"。字段本身没废 —— 合同的原件保护（已签原件/生成稿不许删）、
   * 打样锁定后只许标「后续补充资料」，都靠它做判据，见后端
   * `file/access.py` 的 `PROTECTED_CATEGORIES` / `SUPPLEMENT_CATEGORY`。
   *
   * 只有**真的在用分类**的场景才打开它（目前是打样的附件区：那里的分类要能一眼
   * 看出"制作依据"和"事后补料"的区别）。
   */
  showCategory?: boolean
  enabled?: boolean
  /**
   * 上传/挂载要**目标模块自己的**写权限码，默认按文件中心（file:manage）。
   * 产品这类"写权限跟业务模块走"的对象要显式传 —— 只判 file:manage 会出现
   * "按钮能点、接口 403"。删除按钮不看它（删文件本身是文件中心的动作）。
   */
  writePermission?: string
}

export default function AttachmentPanel({
  businessType,
  businessId,
  category,
  showCategory = false,
  enabled = true,
  writePermission = 'file:manage',
}: Props) {
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  // 上传是两步（先传文件、再挂到业务对象），两道权限都要过：
  // 文件中心那一道（file:manage）+ 目标模块那一道（writePermission）。
  const canWrite = can('file:manage') && can(writePermission)
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
    enabled: enabled && Number.isFinite(businessId),
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

  // 改名（2026-10-08）：只改**展示名**（列表里显示、下载时落成本地名），磁盘不动。
  // 失败时**不关弹窗、保留已输入的名字** —— 比如合同原件会被后端拒，
  // 这时候把人好不容易打的字清掉最讨厌；原因原样显示（别猜，判据在后端）。
  const [renameTarget, setRenameTarget] = useState<FileRow | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const renameMutation = useMutation({
    mutationFn: () => renameFile(renameTarget!.id, renameValue.trim()),
    onSuccess: () => {
      Toast.success('文件名已修改')
      setRenameTarget(null)
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
        {canWrite && (
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
          // 「分类」列只在**这个场景真的在用分类**时才出现（`showCategory`）。
          // 四处现有调用都不打开它：那里上传时没有让用户选分类的地方，
          // 这一列过去永远是"-"。留着只会让人以为"是我漏填了"。
          ...(showCategory
            ? [
                {
                  title: '分类',
                  dataIndex: 'category',
                  width: 110,
                  render: (v: string | null) => v ?? '-',
                },
              ]
            : []),
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
            width: 220,
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
                  <a
                    style={{ color: 'var(--crm-primary)' }}
                    onClick={() => {
                      setRenameValue(record.file_name)
                      setRenameTarget(record)
                    }}
                  >
                    改名
                  </a>
                )}
                {can('file:manage') && (
                  <Popconfirm
                    title="删除后不可恢复，确认？"
                    // 后端还有三道保护会拒绝（合同原件、别处还在引用、一个文件挂了多个
                    // 对象），拒绝时会把原因说清楚——这里不必替它预判，也预判不准。
                    description="如果它是合同原件、或还被别处引用着，系统会拒绝并说明原因。"
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

      {/* 改名弹窗：只改展示名，磁盘上的文件不动 */}
      <Modal
        title="改文件名"
        visible={renameTarget !== null}
        onCancel={() => setRenameTarget(null)}
        onOk={() => {
          if (!renameValue.trim()) {
            Toast.warning('文件名不能为空')
            return
          }
          renameMutation.mutate()
        }}
        confirmLoading={renameMutation.isPending}
        okText="保存"
      >
        <div style={{ marginBottom: 8, color: 'var(--crm-text-3)', fontSize: 12 }}>
          改的是「这个文件在列表里显示的名字」和「别人下载时拿到的名字」；文件内容与存放位置都不变。
        </div>
        <Input
          value={renameValue}
          onChange={setRenameValue}
          maxLength={255}
          placeholder="例如：ZX-6040 塑料周转箱 规格书.pdf"
        />
      </Modal>

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
