import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  Input,
  Modal,
  Popconfirm,
  Select,
  Table,
  Tabs,
  Tag,
  Toast,
  TextArea,
} from '@douyinfe/semi-ui'
import { usePermissions } from '../../shared/hooks/permissions'
import SectionCard from '../../shared/components/SectionCard'
import { listCustomers } from '../../shared/api/customer'
import { listBusinessFiles, uploadFile } from '../../shared/api/file'
import {
  createContractTemplate,
  downloadContractDocument,
  generateContractDocument,
  listContractDocuments,
  listContractTemplates,
  signContractDocument,
  voidContractDocument,
  type ContractDocument,
  type ContractTemplate,
} from '../../shared/api/contract'

const STATUS_TONE: Record<string, 'green' | 'grey' | 'red'> = {
  signed: 'green',
  draft: 'grey',
  void: 'red',
}

export default function DocumentsPage() {
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const canManage = can('order:manage')
  const canSet = can('settings:manage')
  const [activeKey, setActiveKey] = useState('documents')

  const templatesQuery = useQuery({ queryKey: ['contract-templates'], queryFn: listContractTemplates })
  const documentsQuery = useQuery({
    queryKey: ['contract-documents'],
    queryFn: () => listContractDocuments(),
  })
  const customersQuery = useQuery({
    queryKey: ['contract-customers'],
    queryFn: () => listCustomers({ page: 1, page_size: 200 }),
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['contract-templates'] })
    void queryClient.invalidateQueries({ queryKey: ['contract-documents'] })
  }

  // 新建模板版本
  const [templateVisible, setTemplateVisible] = useState(false)
  const [templateForm, setTemplateForm] = useState({
    doc_type: 'contract',
    name: '',
    body: '客户：{{customer.name}}\n签约日：{{today}}\n付款方式：{{extra.付款方式}}',
  })
  const createTemplateMutation = useMutation({
    mutationFn: () => createContractTemplate(templateForm),
    onSuccess: () => {
      Toast.success('模板版本已保存')
      setTemplateVisible(false)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  // 生成合同草稿：空白项一行一条「名称=内容」
  const [generateVisible, setGenerateVisible] = useState(false)
  const [generateForm, setGenerateForm] = useState({
    template_id: undefined as number | undefined,
    customer_id: undefined as number | undefined,
    expiry_date: '',
    extras: '付款方式=',
  })
  const generateMutation = useMutation({
    mutationFn: () => {
      const extra_fields: Record<string, string> = {}
      generateForm.extras
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean)
        .forEach((line) => {
          const idx = line.indexOf('=')
          if (idx > 0) extra_fields[line.slice(0, idx).trim()] = line.slice(idx + 1).trim()
        })
      return generateContractDocument({
        template_id: generateForm.template_id!,
        customer_id: generateForm.customer_id!,
        extra_fields,
        expiry_date: generateForm.expiry_date || null,
      })
    },
    onSuccess: (doc) => {
      Toast.success(`草稿已生成：${doc.doc_no}`)
      setGenerateVisible(false)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  // 登记签署：直接在本弹窗里上传签署扫描件（或从已上传的文件里挑一份），
  // 不再要求用户手填文件 ID——那是给排障用的内部编号，业务看不懂也填不对。
  const [signTarget, setSignTarget] = useState<ContractDocument | null>(null)
  const [signFileId, setSignFileId] = useState<number | null>(null)
  const [signUploading, setSignUploading] = useState(false)
  const signFileInput = useRef<HTMLInputElement>(null)
  const signFilesQuery = useQuery({
    queryKey: ['contract-doc-files', signTarget?.id],
    queryFn: () => listBusinessFiles('contract_document', signTarget!.id),
    enabled: Boolean(signTarget),
  })

  const uploadSignFile = async (file: File) => {
    if (!signTarget) return
    setSignUploading(true)
    try {
      const row = await uploadFile(file, {
        businessType: 'contract_document',
        businessId: signTarget.id,
        category: 'signed',
      })
      setSignFileId(row.id)
      Toast.success('签署件已上传，可直接确认签署')
      void queryClient.invalidateQueries({ queryKey: ['contract-doc-files', signTarget.id] })
    } catch (error) {
      Toast.error(error instanceof Error ? error.message : '上传失败')
    } finally {
      setSignUploading(false)
    }
  }

  const signMutation = useMutation({
    mutationFn: () => signContractDocument(signTarget!.id, { file_id: signFileId! }),
    onSuccess: () => {
      Toast.success('已登记签署')
      setSignTarget(null)
      setSignFileId(null)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const voidMutation = useMutation({
    mutationFn: ({ id, reason }: { id: number; reason: string }) => voidContractDocument(id, reason),
    onSuccess: () => {
      Toast.success('文档已作废')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const templates = templatesQuery.data ?? []
  const documents = documentsQuery.data ?? []

  return (
    <div style={{ padding: 20, maxWidth: 1200, margin: '0 auto' }}>
      <Tabs type="line" activeKey={activeKey} onChange={setActiveKey} tabList={[
        { tab: '文档台账', itemKey: 'documents' },
        { tab: '合同模板', itemKey: 'templates' },
      ]} />

      {activeKey === 'documents' && (
        <SectionCard>
          <div className="toolbar" style={{ marginBottom: 10 }}>
            <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
              生成即快照：客户资料之后修改不影响已生成的合同；签署=上传扫描件登记，下载不等于已签
            </span>
            <div style={{ flex: 1 }} />
            {canManage && (
              <Button theme="solid" onClick={() => setGenerateVisible(true)}>
                从模板生成
              </Button>
            )}
          </div>
          <Table<ContractDocument>
            columns={[
              { title: '编号', dataIndex: 'doc_no', width: 150 },
              { title: '类型', dataIndex: 'doc_type_label', width: 100 },
              { title: '标题', dataIndex: 'title' },
              { title: '客户', dataIndex: 'customer_name', width: 160 },
              {
                title: '状态',
                dataIndex: 'status_label',
                width: 100,
                render: (v: string, record: ContractDocument) => (
                  <Tag color={STATUS_TONE[record.status] ?? 'grey'}>{v}</Tag>
                ),
              },
              { title: '到期日', dataIndex: 'expiry_date', width: 110, render: (v: string | null) => v ?? '-' },
              { title: '签署时间', dataIndex: 'signed_at', width: 130, render: (v: string | null) => (v ? v.slice(0, 10) : '-') },
              ...(canManage
                ? [
                    {
                      title: '操作',
                      width: 130,
                      render: (_: unknown, record: ContractDocument) => (
                        <span style={{ display: 'inline-flex', gap: 12 }}>
                          <a onClick={() => void downloadContractDocument(record)}>下载</a>
                          {record.status === 'draft' && (
                            <a onClick={() => setSignTarget(record)}>登记签署</a>
                          )}
                          {record.status !== 'void' && (
                            <Popconfirm
                              title="作废后不可恢复，确认？"
                              onConfirm={() => voidMutation.mutate({ id: record.id, reason: '页面作废' })}
                            >
                              <a style={{ color: 'var(--crm-danger, #d45)' }}>作废</a>
                            </Popconfirm>
                          )}
                        </span>
                      ),
                    },
                  ]
                : []),
            ]}
            dataSource={documents}
            loading={documentsQuery.isLoading}
            rowKey="id"
            pagination={false}
            empty="还没有合同文档——点「从模板生成」"
          />
        </SectionCard>
      )}

      {activeKey === 'templates' && (
        <SectionCard>
          <div className="toolbar" style={{ marginBottom: 10 }}>
            <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
              占位符：{'{{customer.name}}'}、{'{{order.order_no}}'}、{'{{today}}'}、{'{{extra.自定义项}}'}；改模板=新增版本，旧版本不删
            </span>
            <div style={{ flex: 1 }} />
            {canSet && (
              <Button theme="solid" onClick={() => setTemplateVisible(true)}>
                新建模板版本
              </Button>
            )}
          </div>
          <Table<ContractTemplate>
            columns={[
              { title: '类型', dataIndex: 'doc_type_label', width: 110 },
              { title: '名称', dataIndex: 'name', width: 220 },
              { title: '版本', dataIndex: 'version', width: 80 },
              {
                title: '当前版',
                dataIndex: 'is_current',
                width: 90,
                render: (v: boolean) => (v ? <Tag color="green">当前</Tag> : <Tag color="grey">历史</Tag>),
              },
              {
                title: '正文摘要',
                render: (_: unknown, record: ContractTemplate) =>
                  record.body.length > 60 ? `${record.body.slice(0, 60)}…` : record.body,
              },
            ]}
            dataSource={templates}
            loading={templatesQuery.isLoading}
            rowKey="id"
            pagination={false}
            empty="还没有模板"
          />
        </SectionCard>
      )}

      <Modal
        title="新建模板版本"
        visible={templateVisible}
        onCancel={() => setTemplateVisible(false)}
        onOk={() => createTemplateMutation.mutate()}
        confirmLoading={createTemplateMutation.isPending}
        okText="保存"
        cancelText="取消"
        width={560}
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <div style={{ marginBottom: 4 }}>类型</div>
            <Select
              style={{ width: '100%' }}
              value={templateForm.doc_type}
              onChange={(v) => setTemplateForm({ ...templateForm, doc_type: v as string })}
              optionList={[
                { value: 'contract', label: '销售合同' },
                { value: 'monthly', label: '月结协议' },
              ]}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>模板名称（同名自动累加版本）</div>
            <Input value={templateForm.name} onChange={(v) => setTemplateForm({ ...templateForm, name: v })} />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>正文（用 {'{{}}'} 占位符）</div>
            <TextArea
              rows={8}
              value={templateForm.body}
              onChange={(v) => setTemplateForm({ ...templateForm, body: v })}
            />
          </div>
        </div>
      </Modal>

      <Modal
        title="从模板生成合同"
        visible={generateVisible}
        onCancel={() => setGenerateVisible(false)}
        onOk={() => generateMutation.mutate()}
        confirmLoading={generateMutation.isPending}
        okText="生成草稿"
        cancelText="取消"
        width={560}
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <div style={{ marginBottom: 4 }}>模板（当前版）</div>
            <Select
              style={{ width: '100%' }}
              placeholder="选择模板"
              value={generateForm.template_id}
              onChange={(v) => setGenerateForm({ ...generateForm, template_id: v as number })}
              optionList={templates
                .filter((t) => t.is_current && t.enabled)
                .map((t) => ({ value: t.id, label: `${t.doc_type_label} · ${t.name} v${t.version}` }))}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>客户</div>
            <Select
              style={{ width: '100%' }}
              placeholder="选择客户"
              filter
              value={generateForm.customer_id}
              onChange={(v) => setGenerateForm({ ...generateForm, customer_id: v as number })}
              optionList={(customersQuery.data?.items ?? []).map((c) => ({
                value: c.id,
                label: c.name,
              }))}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>空白项（每行一条「名称=内容」，对应 {'{{extra.名称}}'}）</div>
            <TextArea
              rows={3}
              value={generateForm.extras}
              onChange={(v) => setGenerateForm({ ...generateForm, extras: v })}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>到期日（月结协议建议填写，到期前自动提醒负责人）</div>
            <Input
              placeholder="2026-12-31（可留空）"
              value={generateForm.expiry_date}
              onChange={(v) => setGenerateForm({ ...generateForm, expiry_date: v })}
            />
          </div>
        </div>
      </Modal>

      <Modal
        title={`登记签署：${signTarget?.doc_no ?? ''}`}
        visible={Boolean(signTarget)}
        onCancel={() => setSignTarget(null)}
        onOk={() => signMutation.mutate()}
        confirmLoading={signMutation.isPending}
        okButtonProps={{ disabled: !signFileId }}
        okText="确认签署"
        cancelText="取消"
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            上传签署扫描件（或从已上传的文件里选一份）。签署件与草稿在台账上分开可见。
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <Button
              loading={signUploading}
              disabled={!can('file:manage')}
              onClick={() => signFileInput.current?.click()}
            >
              上传签署件
            </Button>
            <input
              ref={signFileInput}
              type="file"
              style={{ display: 'none' }}
              onChange={(event) => {
                const file = event.target.files?.[0]
                if (file) void uploadSignFile(file)
                event.target.value = ''
              }}
            />
            <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
              {signFileId ? `已选文件 #${signFileId}` : '尚未选择文件'}
            </span>
          </div>
          {(signFilesQuery.data ?? []).length > 0 && (
            <Select
              placeholder="或从已上传的文件里选"
              value={signFileId ?? undefined}
              onChange={(value) => setSignFileId(value as number)}
              optionList={(signFilesQuery.data ?? []).map((row) => ({
                value: row.id,
                label: row.file_name,
              }))}
            />
          )}
        </div>
      </Modal>
    </div>
  )
}
