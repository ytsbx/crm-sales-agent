import { formatBeijingTime } from '../../shared/beijing-time'
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
import { useBlockedNotify } from '../../shared/components/BlockedHint'

const SIZE_UNIT = ['B', 'KB', 'MB', 'GB']

/**
 * 写权限码 → 中文权限名。
 *
 * `writePermission` 传进来的是**权限码**（`file:manage` / `product:manage` /
 * `sample:manage`）。码是给机器看的，界面上不能直接抛给用户 —— 主人定的口径是
 * 说人话、缺哪个权限就写中文名（同 BlockedHint 的用法说明）。
 * 表里没有的码**不猜**：改用「所属模块的管理权限」这种说法，绝不把英文码露到界面上。
 */
const WRITE_PERMISSION_LABEL: Record<string, string> = {
  'file:manage': '文件管理',
  'product:manage': '产品管理',
  'sample:manage': '打样管理',
}

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
  uploadLabel?: string
  accept?: string
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
  onlyCategory?: string
  includeUncategorizedImages?: boolean
  maxFiles?: number
  excludeCategory?: string
  enabled?: boolean
  /**
   * 上传/挂载要**目标模块自己的**写权限码，默认按文件中心（file:manage）。
   * 产品这类"写权限跟业务模块走"的对象要显式传 —— 只判 file:manage 会出现
   * "按钮能点、接口 403"。删除按钮不看它（删文件本身是文件中心的动作）。
   */
  writePermission?: string
  /**
   * 左侧那句说明。**默认那句是通用的**（合同/图纸/回款凭证）——
   * 但不同场景要的东西不一样：打样那边要的是「产品图片（多方位图片）」，
   * 于是由调用方传自己那句（2026-10-16 主人）。
   */
  hint?: string
}

export default function AttachmentPanel({
  businessType,
  businessId,
  category,
  uploadLabel = '上传附件',
  accept,
  showCategory = false,
  onlyCategory,
  includeUncategorizedImages = false,
  maxFiles,
  excludeCategory,
  enabled = true,
  writePermission = 'file:manage',
  hint,
}: Props) {
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  // 上传是两步（先传文件、再挂到业务对象），两道权限都要过：
  // 文件中心那一道（file:manage）+ 目标模块那一道（writePermission）。
  const canWrite = can('file:manage') && can(writePermission)

  // 没有写权限时，界面上从前是**整块上传区不渲染**：空表 + 没有上传按钮 + 界面上
  // 一个字都不说，看着就像"坏了"。主人 2026-10-12：「点又点不了，又不给提示为什么
  // 点不了，类似的情况都要修」—— 上传区不在，也必须说清它为什么不在、找谁能办。
  // ⚠️ 只是补说明：`canWrite` 和 `writePermission` 的判据一个字没动，也没有绕过它。
  const missingWriteReasons: string[] = []
  if (!can('file:manage')) {
    missingWriteReasons.push(
      '你没有上传/修改附件的权限（需要「文件管理」权限），请联系有权限的同事或管理员。',
    )
  } else if (!can(writePermission)) {
    // 两道权限分开说：缺文件中心那道和缺业务模块那道，要去办的事不一样
    // （一个是找管理员开文件权限，一个是找这个模块的负责人）。
    const label = WRITE_PERMISSION_LABEL[writePermission]
    missingWriteReasons.push(
      label
        ? `这个对象的附件要「${label}」权限才能上传或修改，请联系有权限的同事或管理员。`
        : '这个对象的附件要所属模块的管理权限才能上传或修改，请联系有权限的同事或管理员。',
    )
  }
  // 那段灰字本身可点：点了弹一句同样的原因（看得见 + 点得动，见 shared/components/BlockedHint.tsx）
  const notifyWriteBlocked = useBlockedNotify(missingWriteReasons)
  const inputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)

  // 预览状态：objectUrl 用完必须 revoke，否则 blob 会一直留在内存里。
  // 文本类**不进 objectUrl**：直接在下面解成纯文本展示（见 `openPreview`）。
  const [preview, setPreview] = useState<{
    name: string
    mime: string
    url: string
    text?: string
  } | null>(null)
  const [previewing, setPreviewing] = useState(false)

  useEffect(
    () => () => {
      if (preview?.url) window.URL.revokeObjectURL(preview.url)
    },
    [preview],
  )

  const openPreview = async (record: FileRow) => {
    setPreviewing(true)
    try {
      const { blob, mime } = await fetchFilePreview(record.id)
      // 文本类**自己解成纯文本**（第三批审查 P1，2026-10-12）。
      //
      // 从前一律丢给 iframe，等于把"浏览器按内容类型渲染/执行"这条通道留着：
      // 只要哪一步类型判定出错，被渲染的就是可执行内容。纯文本组件不解析标签、
      // 不执行脚本，从根上不算一条执行通道。
      const isText = mime.startsWith('text/')
      const text = isText ? await blob.text() : undefined
      setPreview({
        name: record.file_name,
        mime,
        url: isText ? '' : window.URL.createObjectURL(blob),
        text,
      })
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

  const visibleFiles = (query.data ?? []).filter((file) => (!onlyCategory || file.category === onlyCategory || (includeUncategorizedImages && !file.category && (/^image\//.test(file.mime_type ?? '') || /\.(png|jpe?g|gif|bmp|webp|tiff?)$/i.test(file.file_name)))) && (!excludeCategory || file.category !== excludeCategory))

  const handleUpload = async (file: File) => {
    if (maxFiles && visibleFiles.length >= maxFiles) { Toast.error(`最多上传${maxFiles}张产品图片，请先删除多余图片`); return }
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
      <div className="toolbar" style={{ minWidth: 0, maxWidth: '100%' }}>
        <div style={{ color: 'var(--crm-text-3)', fontSize: 13, flex: '1 1 240px', minWidth: 0 }}>
          {hint ?? '合同、图纸、回款凭证都可以放在这里，单个文件不超过 20 MB'}
        </div>
        <div style={{ flex: 1 }} />
        {canWrite && (
          <>
            <input
              ref={inputRef}
              type="file"
              accept={accept}
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
              {uploadLabel}
            </Button>
          </>
        )}
        {/* 没有写权限时，上传按钮的位置上放这段灰字：为什么传不了、缺哪个权限、找谁办。
            行内的「改名 / 删除」用的是同一道权限（file:manage），所以这句话一并说清，
            用户不会误以为"只是这一份文件不给改"（判据没动，那两个链接照旧按权限渲染）。 */}
        {!canWrite && (
          <div
            data-blocked-click="1"
            className="blocked-hint-click"
            onClick={notifyWriteBlocked}
            style={{ display: 'block', maxWidth: 420, fontSize: 12, lineHeight: 1.7, color: 'var(--crm-text-3)' }}
          >
            {missingWriteReasons.map((text) => (
              <div key={text}>{text}</div>
            ))}
          </div>
        )}
      </div>
      <Table<FileRow>
        style={{ width: '100%', maxWidth: '100%', minWidth: 0 }}
        scroll={{ x: 1000 }}
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
            render: (v: string) => formatBeijingTime(v),
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
        dataSource={visibleFiles}
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

      {/* 预览弹窗：图片用 img；**文本用纯文本块**；PDF 用 iframe（浏览器原生阅读器） */}
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
            ) : preview.text !== undefined ? (
              // 纯文本：不解析标签、不执行脚本，也不加载外部资源
              <pre
                style={{
                  margin: 0,
                  textAlign: 'left',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  fontFamily: 'inherit',
                  fontSize: 13,
                  lineHeight: 1.7,
                }}
              >
                {preview.text || '（这个文件是空的）'}
              </pre>
            ) : (
              <iframe
                src={preview.url}
                title={preview.name}
                // ⚠️ PDF **不能**加 sandbox：实测（本机 Chrome）加了之后浏览器
                // 内置阅读器直接被挡住，两种写法都只剩一个"打不开"的图标
                // （sandbox="" 与 sandbox="allow-same-origin" 都不渲染），
                // 等于把 PDF 预览这个功能做没了。
                //
                // 之所以敢这么写：这一路的类型由**后端**卡死 —— 预览出口只放
                // 图片 / PDF / 纯文本，HTML、SVG、XML 一律拒（第三批审查 P1 修的就是
                // 那个绕过），图片与文本在前端也各自走 <img> 与纯文本块，
                // 所以这个 iframe 实际只会拿到 application/pdf；后端还另加了
                // `Content-Security-Policy: sandbox` 兜所有非 PDF 类型。
                sandbox={preview.mime === 'application/pdf' ? undefined : ''}
                referrerPolicy="no-referrer"
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
