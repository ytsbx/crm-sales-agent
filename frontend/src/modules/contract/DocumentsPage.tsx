import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  Checkbox,
  DatePicker,
  Input,
  Modal,
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
import { listOrders } from '../../shared/api/order'
import { listQuoteVersions, listQuotes } from '../../shared/api/quote'
import { downloadFile, listBusinessFiles, uploadFile } from '../../shared/api/file'
import {
  createContractTemplate,
  downloadContractDocument,
  generateContractDocument,
  getContractDocument,
  listContractDocuments,
  listContractTemplates,
  signContractDocument,
  voidContractDocument,
  type ContractDocument,
  type ContractAmendment,
  type ContractSignedFile,
  type ContractTemplate,
} from '../../shared/api/contract'
import { optionMatcher, withCode } from '../../shared/components/optionMatch'

const STATUS_TONE: Record<string, 'green' | 'grey' | 'red'> = {
  signed: 'green',
  draft: 'grey',
  void: 'red',
}

const DETAIL_LABEL = { fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 2 }
const DETAIL_VALUE = { fontSize: 13 }

/** 模板正文里要求补充的「空白项」：解析 `{{extra.名称}}`，按出现顺序去重。
 *
 * 为什么要从模板里读、而不是让人手写：
 * 模板作者在正文里写 `付款方式：{{extra.付款方式}}`，生成合同的人只需要知道
 * **「付款方式」这一格填什么**。此前界面上给的是一个空文本框 + 一行提示
 * "每行一条「名称=内容」，对应 {{extra.名称}}" —— 等于把模板语法甩给业务员：
 * 既不知道该填哪几项（要回去翻模板正文），也不知道格式对不对
 * （漏个等号、用错中文逗号都只会静默不生效）。
 * 字段名以模板为准，用户只填值，这一类错误就不存在了。
 */
function parseExtraFields(body: string): string[] {
  const names: string[] = []
  const seen = new Set<string>()
  for (const match of body.matchAll(/\{\{\s*extra\.([^}]+?)\s*\}\}/g)) {
    const name = match[1].trim()
    if (name && !seen.has(name)) {
      seen.add(name)
      names.push(name)
    }
  }
  return names
}

export default function DocumentsPage() {
  const queryClient = useQueryClient()
  const { can, isReviewer } = usePermissions()
  const canManage = can('order:manage')
  const canSet = can('settings:manage')
  const [activeKey, setActiveKey] = useState('documents')

  const templatesQuery = useQuery({ queryKey: ['contract-templates'], queryFn: listContractTemplates })
  // 台账真分页：后端原来写死 limit 500，第 501 份合同在页面上永远不出现、也没有提示
  const [docPage, setDocPage] = useState(1)
  const documentsQuery = useQuery({
    queryKey: ['contract-documents', docPage],
    queryFn: () => listContractDocuments({ page: docPage, page_size: 20 }),
  })
  // 客户下拉改成服务端搜索：原来只拉前 200 个客户，第 201 个以后根本选不到
  const [customerKeyword, setCustomerKeyword] = useState('')
  const customersQuery = useQuery({
    queryKey: ['contract-customers', customerKeyword],
    queryFn: () => listCustomers({ keyword: customerKeyword, page: 1, page_size: 50 }),
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

  // 生成合同草稿
  const [generateVisible, setGenerateVisible] = useState(false)
  // 幂等键：打开弹窗时生成一个，同一张弹窗里的重复提交带的是同一个值。
  // 后端据此把第二次请求认成"刚才那份"，不再多建一份带独立编号的草稿。
  const [generateRequestKey, setGenerateRequestKey] = useState('')
  const [generateForm, setGenerateForm] = useState({
    template_id: undefined as number | undefined,
    customer_id: undefined as number | undefined,
    // 正式依据（业务口径：条款可以先备，但登记签署前必须挂上订单或已发出的报价）
    order_id: undefined as number | undefined,
    quote_id: undefined as number | undefined,
    // 合同钉死的报价版本。报价能出 V2/V3，不指定就没法证明金额依据的是哪一版。
    quote_version_id: undefined as number | undefined,
    expiry_date: '',
    effective_date: '',
    // 模板空白项的值：键就是模板里 {{extra.名称}} 的名称。
    // 之所以不再是一个手写的「名称=内容」文本框，见 parseExtraFields 的注释。
    extraValues: {} as Record<string, string>,
  })
  // 当前选中的模板，以及**它要求补充哪几项**（字段名直接从模板正文解析）。
  // 界面上只让用户填值，不再让他自己写 `名称=内容`（见 parseExtraFields）。
  const selectedTemplate = (templatesQuery.data ?? []).find(
    (t) => t.id === generateForm.template_id,
  )
  const extraFieldNames = selectedTemplate ? parseExtraFields(selectedTemplate.body) : []
  const unfilledExtras = extraFieldNames.filter(
    (name) => !(generateForm.extraValues[name] ?? '').trim(),
  )

  // 补充协议 / 续签：从原文档发起，带上 parent_id。原件的正文、签署件都不动，
  // 新文档在台账上能顺着 parent_id 找回出处（"这份补充协议是补哪份合同"）。
  const [generateParent, setGenerateParent] = useState<ContractDocument | null>(null)
  // 续签时是否替代旧协议：**默认不勾**。提前续签、旧协议还在适用期很常见，
  // 一登记就掐掉旧提醒会让还在生效的协议没人管。
  const [supersedeParent, setSupersedeParent] = useState(false)
  // 订单 / 报价 / 报价版本三级联动，全部跟着所选客户走——
  // 不这么做的话，跨客户把别家的单子挂上来，只能等提交时被后端拒掉。
  const genOrdersQuery = useQuery({
    queryKey: ['contract-gen-orders', generateForm.customer_id],
    queryFn: () => listOrders({ customer_id: generateForm.customer_id, page: 1, page_size: 50 }),
    enabled: Boolean(generateForm.customer_id) && generateVisible,
  })
  const genQuotesQuery = useQuery({
    queryKey: ['contract-gen-quotes', generateForm.customer_id],
    queryFn: () => listQuotes({ customer_id: generateForm.customer_id, page: 1, page_size: 50 }),
    enabled: Boolean(generateForm.customer_id) && generateVisible,
  })
  const genVersionsQuery = useQuery({
    queryKey: ['contract-gen-quote-versions', generateForm.quote_id],
    queryFn: () => listQuoteVersions(generateForm.quote_id!),
    enabled: Boolean(generateForm.quote_id) && generateVisible,
  })
  const genOrders = genOrdersQuery.data?.items ?? []
  const genQuotes = genQuotesQuery.data?.items ?? []
  const genVersions = genVersionsQuery.data ?? []
  const selectedOrder = genOrders.find((o) => o.id === generateForm.order_id)
  const generateMutation = useMutation({
    mutationFn: () => {
      // 只提交**当前模板真正要求**的那几项：中途换过模板时，旧模板的字段值还留在
      // state 里，但新模板里没有它 —— 一并提交出去只会变成没人看的杂项。
      // 空值不提交（后端对"没填"与"填了空"的处理一样，都会登记成缺项）。
      const extra_fields: Record<string, string> = {}
      for (const name of extraFieldNames) {
        const value = (generateForm.extraValues[name] ?? '').trim()
        if (value) extra_fields[name] = value
      }
      return generateContractDocument({
        template_id: generateForm.template_id!,
        customer_id: generateForm.customer_id!,
        order_id: generateForm.order_id ?? null,
        quote_id: generateForm.quote_id ?? null,
        quote_version_id: generateForm.quote_version_id ?? null,
        extra_fields,
        expiry_date: generateForm.expiry_date || null,
        effective_date: generateForm.effective_date || null,
        parent_id: generateParent?.id ?? null,
        supersede_parent: supersedeParent,
        request_key: generateRequestKey || undefined,
      })
    },
    onSuccess: (doc) => {
      // 有缺项就不能只报「成功」：正文里留着 {{...}} 占位符，不说一声用户会以为模板坏了。
      const missing = Object.keys(doc.missing_fields ?? {})
      if (missing.length > 0) {
        Toast.warning(
          `草稿已生成，但有 ${missing.length} 处没填上：${missing.slice(0, 3).join('、')}` +
            (missing.length > 3 ? ` 等 ${missing.length} 处` : ''),
        )
      } else {
        Toast.success(`草稿已生成：${doc.doc_no}`)
      }
      setGenerateVisible(false)
      setGenerateParent(null)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const openGenerateModal = (parent: ContractDocument | null) => {
    setGenerateParent(parent)
    // 每次打开都把上一次的续签选项清掉：勾选状态跟着弹窗走，不该跨次留存
    setSupersedeParent(false)
    // 空白项的值也一起清掉：上一次填的"付款方式"是上一份合同的约定，
    // 留到这一次很容易被顺手带进新合同
    setGenerateForm((prev) => ({ ...prev, effective_date: '', extraValues: {} }))
    // 每次打开换一个新键：这一次生成对应这一张弹窗，重试才认得出是同一件事
    setGenerateRequestKey(
      typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}`,
    )
    setGenerateVisible(true)
  }

  // 登记签署：直接在本弹窗里上传签署扫描件（或从已上传的文件里挑一份），
  // 不再要求用户手填文件 ID——那是给排障用的内部编号，业务看不懂也填不对。
  const [signTarget, setSignTarget] = useState<ContractDocument | null>(null)
  const [signFileId, setSignFileId] = useState<number | null>(null)
  const [signUploading, setSignUploading] = useState(false)
  const signFileInput = useRef<HTMLInputElement>(null)
  // 弹窗里当前是哪份合同。上传是异步的，回来时得靠它判断"还是不是同一份合同"——
  // 直接读 state 只会拿到发起上传那一刻的旧值，中途切了合同也照样回填。
  const signTargetIdRef = useRef<number | null>(null)

  const openSignModal = (doc: ContractDocument) => {
    // 换合同必须把上一份选的文件清掉，否则可能把 A 的扫描件登记到 B 上
    signTargetIdRef.current = doc.id
    setSignFileId(null)
    setSignTarget(doc)
  }

  const closeSignModal = () => {
    signTargetIdRef.current = null
    setSignFileId(null)
    setSignUploading(false)
    setSignTarget(null)
    if (signFileInput.current) signFileInput.current.value = ''
  }

  const signFilesQuery = useQuery({
    queryKey: ['contract-doc-files', signTarget?.id],
    // 类型必须写 `contract`：后端挂签署件用的就是它（contract/service.sign_document）。
    // 这里原来写的是 `contract_document`，而后端对没登记的类型**默认拒绝**，
    // 于是这个下拉永远 403、一个已上传的签署件都列不出来。
    queryFn: () => listBusinessFiles('contract', signTarget!.id),
    enabled: Boolean(signTarget),
  })

  const uploadSignFile = async (file: File) => {
    if (!signTarget) return
    const startedFor = signTarget.id
    setSignUploading(true)
    try {
      const row = await uploadFile(file, {
        businessType: 'contract',
        businessId: startedFor,
        category: 'signed',
      })
      // 上传期间用户可能已经关了弹窗、或切到别的合同：那份文件不能算在当前这份头上
      if (signTargetIdRef.current !== startedFor) {
        Toast.info('文件已上传，但你已切换到别的合同，这次没有替你选中它')
        return
      }
      setSignFileId(row.id)
      Toast.success('签署件已上传，可直接确认签署')
      void queryClient.invalidateQueries({ queryKey: ['contract-doc-files', startedFor] })
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
      closeSignModal()
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  // 详情弹窗：抬头快照 + 依据（订单 / 报价版本）+ 关系链（基于谁、被哪几份补充）+
  // 签署原件。这几样散在一张列表上看不出来，得有个地方集中交代（审查阶段 C 第 5 点）。
  // 单独拿它当"看签署原件"的入口也是这个道理：只给一个「下载」，
  // 用户会以为拿到的是签回来的那一份，实际是我们自己生成的稿子，事后对账就会扯皮。
  const [detailTarget, setDetailTarget] = useState<ContractDocument | null>(null)
  const detailQuery = useQuery({
    queryKey: ['contract-detail', detailTarget?.id],
    queryFn: () => getContractDocument(detailTarget!.id),
    enabled: Boolean(detailTarget),
  })
  const detail = detailQuery.data
  const signedFiles: ContractSignedFile[] = detail?.signed_files ?? []
  const amendments: ContractAmendment[] = detail?.amendments ?? []

  // 作废要填真实原因：原来前端写死"页面作废"，台账和审计里全是这四个字，等于没写
  const [voidTarget, setVoidTarget] = useState<ContractDocument | null>(null)
  const [voidReason, setVoidReason] = useState('')
  const closeVoidModal = () => {
    setVoidTarget(null)
    setVoidReason('')
  }
  const voidMutation = useMutation({
    mutationFn: ({ id, reason }: { id: number; reason: string }) => voidContractDocument(id, reason),
    onSuccess: () => {
      Toast.success('文档已作废')
      closeVoidModal()
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const templates = templatesQuery.data ?? []
  // 已选签署件的文件名：确认前要让用户看清"签的是哪份文件"，光给内部编号看不懂
  const selectedSignFile = (signFilesQuery.data ?? []).find((row) => row.id === signFileId)
  const documents = documentsQuery.data?.items ?? []

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
              生成即快照：客户资料之后修改不影响已生成的合同；「下载生成稿」和「看签署原件」
              是两个入口，下载不等于已签
            </span>
            <div style={{ flex: 1 }} />
            {canManage && (
              <Button theme="solid" onClick={() => openGenerateModal(null)}>
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
              {
                // 「依据」= 这份合同是照哪一版报价 / 哪张订单签的。
                // 报价能出 V2/V3，不写清版本，台账上光看单号证明不了金额依据。
                title: '依据',
                width: 170,
                render: (_: unknown, record: ContractDocument) => {
                  const header = record.header_snapshot
                  if (record.quote_version_no) {
                    return `${header?.quote_no ?? '报价'} V${record.quote_version_no}`
                  }
                  if (header?.order_no) return `订单 ${header.order_no}`
                  if (header?.quote_no) return `报价 ${header.quote_no}`
                  return <span style={{ color: 'var(--crm-text-3)' }}>未绑定</span>
                },
              },
              {
                // 「下载」不跟管理操作挤在一起：下载接口要的是 order:view，
                // 原来整列挂在 canManage 下，只读用户连下载入口都看不见。
                title: '操作',
                width: 260,
                render: (_: unknown, record: ContractDocument) => (
                  <span style={{ display: 'inline-flex', gap: 12, flexWrap: 'wrap' }}>
                    <a onClick={() => void downloadContractDocument(record)}>下载生成稿</a>
                    {/* 详情：抬头快照、依据、关系链、签署原件都收在这里。
                        签署原件**光看状态看不出来**，必须点进来才知道拿到的到底是哪一份。 */}
                    <a onClick={() => setDetailTarget(record)}>详情</a>
                    {canManage && record.status === 'draft' && (
                      <a onClick={() => openSignModal(record)}>登记签署</a>
                    )}
                    {/* 补充协议 / 续签：从原文档发起，新文档带 parent_id 指回来。
                        原件不动，旧版永远查得到。 */}
                    {canManage && record.status !== 'void' && (
                      <a onClick={() => openGenerateModal(record)}>补充/续签</a>
                    )}
                    {/* 已签合同的作废要主管：不是主管就不给入口，省得点完吃一个 403 */}
                    {canManage &&
                      record.status !== 'void' &&
                      (record.status !== 'signed' || isReviewer) && (
                        <a
                          style={{ color: 'var(--crm-danger, #d45)' }}
                          onClick={() => {
                            setVoidTarget(record)
                            setVoidReason('')
                          }}
                        >
                          作废
                        </a>
                      )}
                  </span>
                ),
              },
            ]}
            dataSource={documents}
            loading={documentsQuery.isLoading}
            rowKey="id"
            pagination={{
              currentPage: docPage,
              pageSize: 20,
              total: documentsQuery.data?.total ?? 0,
              onPageChange: setDocPage,
            }}
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
            {/* 写模板的人能立刻看到"这份模板会让业务员填哪几项"——
                占位符名写错了（少个括号、extra 拼成 extta）在这里就能发现，
                否则要等生成合同时正文里留着一串 {{}} 才看得出来。 */}
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginTop: 4 }}>
              业务员生成时要补充：
              {parseExtraFields(templateForm.body).join('、') ||
                '（这个模板没有空项，业务员直接生成即可）'}
            </div>
          </div>
        </div>
      </Modal>

      <Modal
        title={generateParent ? `补充/续签：基于 ${generateParent.doc_no}` : '从模板生成合同'}
        visible={generateVisible}
        onCancel={() => {
          setGenerateVisible(false)
          setGenerateParent(null)
        }}
        onOk={() => generateMutation.mutate()}
        confirmLoading={generateMutation.isPending}
        okText="生成草稿"
        cancelText="取消"
        width={560}
      >
        <div style={{ display: 'grid', gap: 12 }}>
          {generateParent && (
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
              这份新文档会挂在 <b>{generateParent.doc_no}</b> 下面（原合同不动，历史版本保留）。
            </div>
          )}
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
              placeholder="搜索客户名称（服务端搜索，不止前 200 个）"
              filter={optionMatcher}
              // remote：不在本地过滤，把关键字交给服务端查 —— 客户上千条时本地那点数据不够用
              remote
              loading={customersQuery.isFetching}
              onSearch={setCustomerKeyword}
              value={generateForm.customer_id}
              onChange={(v) =>
                setGenerateForm({
                  ...generateForm,
                  customer_id: v as number,
                  // 换客户必须把依据一起清掉：上一家的订单/报价挂到这一家头上，
                  // 提交时会被后端拒掉，但让用户先选好再被拒更莫名其妙。
                  order_id: undefined,
                  quote_id: undefined,
                  quote_version_id: undefined,
                })
              }
              optionList={(customersQuery.data?.items ?? []).map((c) => ({
                value: c.id,
                label: withCode(c.name, c.id),
              }))}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>关联订单（可选）</div>
            <Select
              style={{ width: '100%' }}
              placeholder={generateForm.customer_id ? '这张客户下的正式订单' : '先选客户'}
              filter={optionMatcher}
              disabled={!generateForm.customer_id}
              loading={genOrdersQuery.isFetching}
              value={generateForm.order_id}
              onChange={(v) => setGenerateForm({ ...generateForm, order_id: v as number })}
              optionList={genOrders.map((o) => ({
                value: o.id,
                label: `${o.order_no} · ¥${o.total_amount}`,
              }))}
            />
            {selectedOrder?.quote_version_id && (
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginTop: 4 }}>
                这张订单是照某个报价版本转过来的，生成时会自动采用那一版（不用再选）
              </div>
            )}
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>关联报价（可选）</div>
            <Select
              style={{ width: '100%' }}
              placeholder={generateForm.customer_id ? '这张客户下的报价单' : '先选客户'}
              filter={optionMatcher}
              disabled={!generateForm.customer_id}
              loading={genQuotesQuery.isFetching}
              value={generateForm.quote_id}
              onChange={(v) =>
                setGenerateForm({
                  ...generateForm,
                  quote_id: v as number,
                  // 换报价就清掉版本：那是上一张报价的版本号，挂到新报价上必然对不上
                  quote_version_id: undefined,
                })
              }
              optionList={genQuotes.map((q) => ({
                value: q.id,
                label: `${q.quote_no}（${q.status_label}）`,
              }))}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>报价版本（决定合同上的金额与条款）</div>
            <Select
              style={{ width: '100%' }}
              placeholder={generateForm.quote_id ? '选具体版本' : '先选报价单'}
              disabled={!generateForm.quote_id}
              loading={genVersionsQuery.isFetching}
              value={generateForm.quote_version_id}
              onChange={(v) => setGenerateForm({ ...generateForm, quote_version_id: v as number })}
              optionList={genVersions.map((v) => ({
                value: v.id,
                label: `V${v.version_no} · ¥${v.total_amount}`,
              }))}
            />
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginTop: 4 }}>
              合同会钉死这一版：报价之后出 V2、V3 都不会改动已生成的合同。
              不选也能先备条款，但登记签署前必须补上正式依据。
            </div>
          </div>
          {/* 模板空白项：字段名**从模板正文里读出来**，用户只填值。
              此前是一个空文本框 + 提示"每行一条「名称=内容」，对应 {{extra.名称}}"
              —— 业务员既不知道该填哪几项，也不知道格式对不对。 */}
          {extraFieldNames.length > 0 ? (
            <div>
              <div style={{ marginBottom: 4 }}>
                需要补充的信息（这个模板有 {extraFieldNames.length} 处待填）
              </div>
              <div style={{ display: 'grid', gap: 8 }}>
                {extraFieldNames.map((name) => (
                  <div
                    key={name}
                    style={{
                      display: 'grid',
                      gridTemplateColumns: 'minmax(0, 110px) minmax(0, 1fr)',
                      gap: 8,
                      alignItems: 'center',
                    }}
                  >
                    <div style={{ fontSize: 13, color: 'var(--crm-text-2)' }}>{name}</div>
                    <Input
                      value={generateForm.extraValues[name] ?? ''}
                      placeholder={`填写「${name}」`}
                      onChange={(v) =>
                        setGenerateForm({
                          ...generateForm,
                          extraValues: { ...generateForm.extraValues, [name]: v },
                        })
                      }
                    />
                  </div>
                ))}
              </div>
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginTop: 6 }}>
                {unfilledExtras.length > 0
                  ? `还有 ${unfilledExtras.length} 项没填（${unfilledExtras.join('、')}）。` +
                    '不填也能先生成草稿，正文里会留着空白标记，确认后再补。'
                  : '这几项会填进合同正文对应的位置。'}
              </div>
            </div>
          ) : selectedTemplate ? (
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
              这个模板没有需要临时补充的空项，直接生成即可。
            </div>
          ) : null}
          <div>
            <div style={{ marginBottom: 4 }}>到期日（月结协议建议填写，到期前自动提醒负责人）</div>
            <DatePicker
              type="date"
              format="yyyy-MM-dd"
              showClear
              style={{ width: '100%' }}
              placeholder="选择到期日（可留空）"
              value={generateForm.expiry_date ? new Date(generateForm.expiry_date) : undefined}
              onChange={(_, dateStr) =>
                setGenerateForm({ ...generateForm, expiry_date: (dateStr as string) || '' })
              }
            />
          </div>
          {generateParent && (
            <div>
              <div style={{ marginBottom: 4 }}>协议生效日（续签建议填写）</div>
              <DatePicker
                type="date"
                format="yyyy-MM-dd"
                showClear
                style={{ width: '100%' }}
                placeholder="选择生效日（可留空）"
                value={generateForm.effective_date ? new Date(generateForm.effective_date) : undefined}
                onChange={(_, dateStr) =>
                  setGenerateForm({ ...generateForm, effective_date: (dateStr as string) || '' })
                }
              />
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginTop: 4 }}>
                只记到期日处理不了"提前签、未来才生效"——那种情况下旧协议还得继续适用一段。
              </div>
            </div>
          )}
          {generateParent && generateParent.doc_type === 'monthly' && (
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
              <Checkbox
                checked={supersedeParent}
                onChange={(e) => setSupersedeParent(Boolean(e.target.checked))}
              />
              <div>
                <div>替代原协议 {generateParent.doc_no}</div>
                <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                  勾上才会结束原协议的在办提醒。原协议本身**不改状态、不动作废**（那是另一回事）。
                  提前续签、新协议还没生效时别勾——否则还在适用的旧协议就没人管了。
                </div>
              </div>
            </div>
          )}
        </div>
      </Modal>

      <Modal
        title={`登记签署：${signTarget?.doc_no ?? ''}`}
        visible={Boolean(signTarget)}
        onCancel={closeSignModal}
        onOk={() => signMutation.mutate()}
        confirmLoading={signMutation.isPending}
        // 上传没结束时不许确认：这会儿 signFileId 可能是上一份的，也可能还是空的
        okButtonProps={{ disabled: !signFileId || signUploading }}
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
              {signFileId
                ? `已选：${selectedSignFile?.file_name ?? `文件 #${signFileId}`}`
                : '尚未选择文件'}
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

      <Modal
        title={`作废：${voidTarget?.doc_no ?? ''}`}
        visible={Boolean(voidTarget)}
        onCancel={closeVoidModal}
        onOk={() => voidMutation.mutate({ id: voidTarget!.id, reason: voidReason.trim() })}
        confirmLoading={voidMutation.isPending}
        okButtonProps={{ disabled: !voidReason.trim(), type: 'danger' }}
        okText="确认作废"
        cancelText="取消"
      >
        <div style={{ display: 'grid', gap: 8 }}>
          <div style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
            作废后不可恢复，已生成的原件仍保留。原因会写进台账和审计，请写清楚真实原因
            —— 原来这里固定写死「页面作废」，等于什么也没说。
          </div>
          <TextArea
            placeholder="作废原因（必填）"
            value={voidReason}
            onChange={setVoidReason}
            rows={3}
            maxCount={255}
          />
        </div>
      </Modal>

      <Modal
        title={`文档详情：${detailTarget?.doc_no ?? ''}`}
        visible={Boolean(detailTarget)}
        onCancel={() => setDetailTarget(null)}
        footer={null}
        width={620}
      >
        {detailQuery.isLoading && <div>读取中…</div>}
        {detail && (
          <div style={{ display: 'grid', gap: 14 }}>
            <div>
              <div style={DETAIL_LABEL}>抬头（生成时固定，客户/公司后来改名也不影响下载）</div>
              <div style={DETAIL_VALUE}>
                {detail.header_snapshot?.company_name || '（未设置公司抬头）'} · 客户：
                {detail.header_snapshot?.customer_name ?? detail.customer_name ?? '-'}
              </div>
            </div>
            <div>
              <div style={DETAIL_LABEL}>依据</div>
              <div style={DETAIL_VALUE}>
                {detail.quote_version_no
                  ? `${detail.header_snapshot?.quote_no ?? '报价'} V${detail.quote_version_no}`
                  : detail.header_snapshot?.order_no
                    ? `订单 ${detail.header_snapshot.order_no}`
                    : '未绑定——登记签署前必须补上正式依据'}
                {detail.effective_date ? ` · 生效日 ${detail.effective_date}` : ''}
                {detail.expiry_date ? ` · 到期日 ${detail.expiry_date}` : ''}
              </div>
            </div>
            <div>
              <div style={DETAIL_LABEL}>关系链</div>
              <div style={DETAIL_VALUE}>
                {detail.parent_doc_no
                  ? `基于 ${detail.parent_doc_no}（${detail.doc_type === 'monthly' ? '续签' : '补充协议'}）`
                  : '无上级文档'}
                {amendments.length > 0 && (
                  <div style={{ marginTop: 4 }}>
                    被以下文档补充 / 续签：
                    {amendments.map((row) => `${row.doc_no}（${row.status_label}）`).join('、')}
                  </div>
                )}
              </div>
            </div>
            <div>
              <div style={DETAIL_LABEL}>签署原件（客户签回来、上传登记的那一份）</div>
              {signedFiles.length === 0 ? (
                <div style={{ color: 'var(--crm-danger, #d45)', fontSize: 12 }}>
                  {detail.status === 'signed'
                    ? '没有找到签署原件：登记时挂上的文件可能已被删除或解绑。不能拿生成稿当签署件用，请向经手人确认原件去向。'
                    : '还没有登记签署。'}
                </div>
              ) : (
                signedFiles.map((row) => (
                  <div key={row.file_id} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    {can('file:view') ? (
                      <a onClick={() => void downloadFile(row.file_id, row.file_name)}>{row.file_name}</a>
                    ) : (
                      <span>{row.file_name}</span>
                    )}
                    <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                      {Math.max(1, Math.round(row.size / 1024))} KB
                      {row.attached_at ? ` · 登记于 ${row.attached_at.slice(0, 10)}` : ''}
                    </span>
                  </div>
                ))
              )}
            </div>
            {Object.keys(detail.missing_fields ?? {}).length > 0 && (
              <div>
                <div style={DETAIL_LABEL}>生成时没填上的地方（正文里留着占位符）</div>
                <div style={{ fontSize: 12, color: 'var(--crm-warning, #d68000)' }}>
                  {Object.entries(detail.missing_fields)
                    .map(([token, reason]) => `${token}（${reason}）`)
                    .join('；')}
                </div>
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  )
}
