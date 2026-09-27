import { useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button, Modal, Tag, Toast } from '@douyinfe/semi-ui'

/**
 * CSV 批量导入按钮组：下载模板 + 批量导入 + 结果弹窗。
 *
 * 给价格中心的 成本/价格规则/客户特殊价 等多个 Tab 复用，
 * 交互与产品/客户导入一致：下载模板 → 填好 → 上传 → 逐行结果反馈。
 */
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
  /** 导入成功后要刷新的 react-query key 列表 */
  invalidateQueryKeys: string[]
  onDone?: () => void
}) {
  const queryClient = useQueryClient()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [importing, setImporting] = useState(false)
  const [result, setResult] = useState<{
    created_count: number
    skipped_count: number
    failed_count: number
    failed: Array<{ name: string; reason: string }>
  } | null>(null)

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

  const handleImport = async (file: File) => {
    setImporting(true)
    try {
      const { default: axios } = await import('axios')
      const formData = new FormData()
      formData.append('file', file)
      const response = await axios.post(importUrl, formData, { headers: tokenHeader() })
      const body = response.data
      if (body.code !== 0) throw new Error(body.message)
      Toast.success(body.message)
      setResult(body.data)
      for (const key of invalidateQueryKeys) {
        void queryClient.invalidateQueries({ queryKey: [key] })
      }
      onDone?.()
    } catch (error) {
      Toast.error(error instanceof Error ? error.message : '导入失败')
    } finally {
      setImporting(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
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
          if (file) void handleImport(file)
        }}
      />
      <Modal
        title="导入结果"
        visible={Boolean(result)}
        onCancel={() => setResult(null)}
        onOk={() => setResult(null)}
        okText="知道了"
        cancelText="关闭"
        footer={null}
        width={620}
      >
        {result && (
          <div style={{ display: 'grid', gap: 12 }}>
            <div style={{ display: 'flex', gap: 16 }}>
              <span className="chip chip-primary">成功 {result.created_count} 条</span>
              <span className="chip chip-warning">
                跳过/失败 {result.skipped_count + result.failed_count} 条
              </span>
            </div>
            {result.failed?.length > 0 && (
              <div style={{ maxHeight: 260, overflow: 'auto' }}>
                {result.failed.map((row, index) => (
                  <div key={index} style={{ marginBottom: 6 }}>
                    <Tag color="red" style={{ marginRight: 8 }}>
                      第 {row.row} 行{row.name ? ` · ${row.name}` : ''}
                    </Tag>
                    {row.reason}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </Modal>
    </>
  )
}
