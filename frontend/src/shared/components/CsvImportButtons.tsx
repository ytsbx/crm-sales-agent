import { useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button, Modal, Table, Tag, Toast } from '@douyinfe/semi-ui'

/**
 * CSV 批量导入按钮组（方案 §6 / A08）：下载模板 → 上传预览 → 确认导入。
 *
 * 两步走：第一步带 preview=1 只校验不落库，展示"将导入/跳过/失败"与失败明细；
 * 用户确认后再真正导入。失败行支持下载 CSV 错误清单（超出弹窗展示量也不丢）。
 */
interface ImportRowIssue {
  row: number
  name: string
  reason: string
}

interface ImportSummary {
  total: number
  created_count: number
  skipped_count: number
  failed_count: number
  created?: Array<{ row: number; name?: string; sku_code?: string }>
  failed: ImportRowIssue[]
  preview?: boolean
}

export default function CsvImportButtons({
  templateUrl,
  templateName,
  importUrl,
  invalidateQueryKeys,
  onDone,
}: {
  templateUrl: string
  templateName: string
  importUrl: string
  invalidateQueryKeys: string[]
  onDone?: () => void
}) {
  const queryClient = useQueryClient()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [importing, setImporting] = useState(false)
  const [preview, setPreview] = useState<ImportSummary | null>(null)
  const [previewFile, setPreviewFile] = useState<File | null>(null)
  const [result, setResult] = useState<ImportSummary | null>(null)

  const tokenHeader = () => {
    const token = JSON.parse(localStorage.getItem('crm-auth') ?? '{}')?.state?.token
    return token ? { Authorization: `Bearer ${token}` } : {}
  }

  const downloadCsv = async (url: string, filename: string) => {
    const { default: axios } = await import('axios')
    const response = await axios.get(url, { responseType: 'blob', headers: tokenHeader() })
    const blobUrl = window.URL.createObjectURL(new Blob([response.data]))
    const link = document.createElement('a')
    link.href = blobUrl
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(blobUrl)
  }

  const downloadFailedCsv = (rows: ImportRowIssue[]) => {
    const header = '行号,标识,失败原因'
    const lines = rows.map((r) => `${r.row},"${(r.name ?? '').replace(/"/g, '""')}","${r.reason.replace(/"/g, '""')}"`)
    const blob = new Blob(['\ufeff' + [header, ...lines].join('\n')], { type: 'text/csv;charset=utf-8' })
    const blobUrl = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = blobUrl
    link.download = `导入失败清单-${new Date().toISOString().slice(0, 10)}.csv`
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(blobUrl)
  }

  const postImport = async (file: File, isPreview: boolean) => {
    const { default: axios } = await import('axios')
    const formData = new FormData()
    formData.append('file', file)
    if (isPreview) formData.append('preview', 'true')
    const response = await axios.post(importUrl, formData, { headers: tokenHeader() })
    const body = response.data
    if (body.code !== 0) throw new Error(body.message)
    return body.data as ImportSummary
  }

  const handleFile = async (file: File) => {
    setImporting(true)
    try {
      const summary = await postImport(file, true)
      setPreview(summary)
      setPreviewFile(file)
    } catch (error) {
      Toast.error(error instanceof Error ? error.message : '文件解析失败')
    } finally {
      setImporting(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const confirmImport = async () => {
    if (!previewFile) return
    setImporting(true)
    try {
      const summary = await postImport(previewFile, false)
      setPreview(null)
      setPreviewFile(null)
      setResult(summary)
      for (const key of invalidateQueryKeys) {
        void queryClient.invalidateQueries({ queryKey: [key] })
      }
      onDone?.()
    } catch (error) {
      Toast.error(error instanceof Error ? error.message : '导入失败')
    } finally {
      setImporting(false)
    }
  }

  const failedTable = (rows: ImportRowIssue[]) => (
    <Table
      columns={[
        { title: '行号', dataIndex: 'row', width: 70 },
        { title: '标识', dataIndex: 'name', width: 140 },
        { title: '失败原因', dataIndex: 'reason' },
      ]}
      dataSource={rows}
      pagination={rows.length > 8 ? { pageSize: 8 } : false}
      rowKey={(r?: ImportRowIssue) => `${r?.row}-${r?.reason}`}
      maxHeight={260}
    />
  )

  return (
    <>
      <Button onClick={() => downloadCsv(templateUrl, templateName)}>下载模板</Button>
      <Button loading={importing} onClick={() => fileInputRef.current?.click()}>
        批量导入
      </Button>
      <input
        ref={fileInputRef}
        type="file"
        accept=".csv"
        style={{ display: 'none' }}
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) void handleFile(file)
        }}
      />

      {/* 预览弹窗：只校验未落库，用户确认后才真正导入 */}
      <Modal
        title="导入预览（尚未写入）"
        visible={Boolean(preview)}
        onCancel={() => {
          setPreview(null)
          setPreviewFile(null)
        }}
        onOk={() => void confirmImport()}
        okText={preview?.failed_count ? `仍要导入 ${preview.created_count} 条` : `确认导入 ${preview?.created_count ?? 0} 条`}
        cancelText="取消"
        confirmLoading={importing}
        width={680}
      >
        {preview && (
          <div style={{ display: 'grid', gap: 12 }}>
            <div style={{ display: 'flex', gap: 16 }}>
              <span className="chip chip-primary">将导入 {preview.created_count} 条</span>
              <span className="chip chip-warning">
                跳过 {preview.skipped_count} 条 · 失败 {preview.failed_count} 条
              </span>
            </div>
            {preview.failed.length > 0 && (
              <>
                {failedTable(preview.failed)}
                <Button size="small" onClick={() => downloadFailedCsv(preview.failed)}>
                  下载失败清单（{preview.failed.length} 条）
                </Button>
              </>
            )}
          </div>
        )}
      </Modal>

      {/* 结果弹窗 */}
      <Modal
        title="导入结果"
        visible={Boolean(result)}
        onCancel={() => setResult(null)}
        onOk={() => setResult(null)}
        okText="知道了"
        cancelText="关闭"
        footer={null}
        width={680}
      >
        {result && (
          <div style={{ display: 'grid', gap: 12 }}>
            <div style={{ display: 'flex', gap: 16 }}>
              <span className="chip chip-primary">成功 {result.created_count} 条</span>
              <span className="chip chip-warning">
                跳过 {result.skipped_count} 条 · 失败 {result.failed_count} 条
              </span>
            </div>
            {result.failed.length > 0 && (
              <>
                {failedTable(result.failed)}
                <Button size="small" onClick={() => downloadFailedCsv(result.failed)}>
                  下载失败清单（{result.failed.length} 条）
                </Button>
              </>
            )}
          </div>
        )}
      </Modal>
    </>
  )
}
