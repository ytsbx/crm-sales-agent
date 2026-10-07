import { useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button, Modal, Table, Toast } from '@douyinfe/semi-ui'

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
  updated_count?: number
  skipped_count: number
  failed_count: number
  created?: Array<{ row: number; name?: string; sku_code?: string }>
  failed: ImportRowIssue[]
  /** 失败行数超过上限时后端只回前 N 条，并把这两个标识一并给出（§7.6）。 */
  failed_truncated?: boolean
  failed_total?: number
  preview?: boolean
  /** 预览响应带回来的快照，确认导入时必须原样送回，否则后端无从比对。 */
  preview_token?: string
}

/** 后端 40902（版本冲突）—— 预览与本次执行结论不一致时用它，含义是"重新预览"。 */
const CODE_VERSION_CONFLICT = 40902

class ImportError extends Error {
  code: number

  constructor(message: string, code: number) {
    super(message)
    this.name = 'ImportError'
    this.code = code
  }
}

/**
 * 从 axios 错误里取出**后端那句话**。
 *
 * 这个组件是直接用 axios 发请求的（`importUrl` 已含 `/api/v1` 前缀，
 * 走不了带 baseURL 的统一客户端），所以拿不到响应拦截器的错误归一 ——
 * 不这么取的话，界面上只会显示 "Request failed with status code 409"，
 * 而真正有用的「结论与预览不一致，请重新预览」被丢掉了。
 */
function importErrorMessage(error: unknown, fallback: string): { message: string; code: number } {
  const data = (error as { response?: { data?: { message?: string; code?: number } } })?.response
    ?.data
  if (data && typeof data.message === 'string' && data.message.trim()) {
    return { message: data.message, code: Number(data.code ?? 0) }
  }
  if (error instanceof Error && error.message) return { message: error.message, code: 0 }
  return { message: fallback, code: 0 }
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

  const postImport = async (file: File, isPreview: boolean, previewToken?: string) => {
    const { default: axios } = await import('axios')
    const formData = new FormData()
    formData.append('file', file)
    if (isPreview) formData.append('preview', 'true')
    // §7.6：确认导入必须带上预览快照。后端据此核对「文件本身 + 每行结论」
    // 是否仍与预览一致；不带的话它只能看到一次普通导入，预览就等于没校验。
    if (!isPreview && previewToken) formData.append('preview_token', previewToken)
    try {
      const response = await axios.post(importUrl, formData, { headers: tokenHeader() })
      const body = response.data
      if (body.code !== 0) throw new ImportError(body.message ?? '导入失败', Number(body.code ?? 0))
      return body.data as ImportSummary
    } catch (error) {
      if (error instanceof ImportError) throw error
      const { message, code } = importErrorMessage(error, isPreview ? '文件解析失败' : '导入失败')
      throw new ImportError(message, code)
    }
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
    const previewToken = preview?.preview_token
    setImporting(true)
    try {
      const summary = await postImport(previewFile, false, previewToken)
      setPreview(null)
      setPreviewFile(null)
      setResult(summary)
      for (const key of invalidateQueryKeys) {
        void queryClient.invalidateQueries({ queryKey: [key] })
      }
      onDone?.()
    } catch (error) {
      // 确认失败（尤其是"结论与预览不一致"40902）时后端**一条都没写**，
      // 旧预览已经作废：关掉它、让用户重新选文件预览，
      // 而不是留着一个越点越不对的"确认导入"按钮。
      setPreview(null)
      setPreviewFile(null)
      if (fileInputRef.current) fileInputRef.current.value = ''
      const hint =
        error instanceof ImportError && error.code === CODE_VERSION_CONFLICT
          ? '请重新选择文件预览后再确认'
          : '导入失败'
      Toast.error(error instanceof Error ? error.message : hint)
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

  /**
   * 失败清单区块（§7.6）。**截断必须说出来**：后端单次响应里的失败明细有上限，
   * 超出部分不在响应里 —— 界面上要如实写清「共 N 条、这里只有前 M 条」，
   * 不能让用户以为下载到的就是全部，否则他会按这份清单去改文件、漏掉剩下的行。
   */
  const failedSection = (summary: ImportSummary) => {
    if (summary.failed.length === 0) return null
    const total = summary.failed_total ?? summary.failed.length
    const truncated = Boolean(summary.failed_truncated) || total > summary.failed.length
    return (
      <>
        {failedTable(summary.failed)}
        {truncated && (
          <div className="label-hint">
            失败共 {total} 条，此处仅列出前 {summary.failed.length} 条（其余未包含在本次响应里）。
          </div>
        )}
        <div>
          <Button size="small" onClick={() => downloadFailedCsv(summary.failed)}>
            下载失败清单（{summary.failed.length} 条
            {truncated ? `，共 ${total} 条` : ''}）
          </Button>
        </div>
      </>
    )
  }

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
            {failedSection(preview)}
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
            {failedSection(result)}
          </div>
        )}
      </Modal>
    </>
  )
}
