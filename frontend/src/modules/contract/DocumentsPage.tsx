import { formatBeijingDate, formatBeijingTime } from '../../shared/beijing-time'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import CustomerApproval, { ApprovalHistory, ContractBasis } from './CustomerApproval'
import { api } from '../../shared/api/client'
import { templateFieldLabel } from './template-fields'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  DatePicker,
  Input,
  Modal,
  Select,
  Table,
  Tag,
  Toast,
  TextArea,
} from '@douyinfe/semi-ui'
import { usePermissions } from '../../shared/hooks/permissions'
import SectionCard from '../../shared/components/SectionCard'
import BlockedHint, { useBlockedNotify } from '../../shared/components/BlockedHint'
import GuardedButton from '../../shared/components/GuardedButton'
import { blockedReasons } from '../../shared/components/blocked-hint-reasons'
import { listCustomers } from '../../shared/api/customer'
import { listOrders } from '../../shared/api/order'
import { listQuoteVersions, listQuotes } from '../../shared/api/quote'
import { downloadFile, listBusinessFiles, uploadFile } from '../../shared/api/file'
import { downloadContractDoc } from '../../shared/download-contract'
import {
  uploadContractDocument,
  getContractDocument,
  listContractDocuments,
  signContractDocument,
  withdrawContractDingTalkApproval,
  voidContractDocument,
  type ContractDocument,
  type ContractAmendment,
  type ContractSignedFile,
} from '../../shared/api/contract'
import { optionMatcher, optionsWithCodeOnDuplicate } from '../../shared/components/optionMatch'
import {
  AdoptOaInstanceModal,
  ResolveOaActions,
} from '../../shared/components/ResolveOaActions'
import { newRequestKey } from '../../shared/api/requestKey'
import SyncOaButton from '../../shared/components/SyncOaButton'
import type { OaApprovalInstance } from '../../shared/api/dingtalk'

const STATUS_TONE: Record<string, 'green' | 'grey' | 'red'> = {
  signed: 'green',
  draft: 'grey',
  void: 'red',
}

const DETAIL_LABEL = { fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 2 }
const DETAIL_VALUE = { fontSize: 13 }

export default function DocumentsPage() {
  const queryClient = useQueryClient()
  const { can, isReviewer } = usePermissions()
  const canManage = can('order:manage')

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
    void queryClient.invalidateQueries({ queryKey: ['contract-documents'] })
  }

  // 上传合同原文件，并明确条款版本的提供方
  const [uploadVisible, setUploadVisible] = useState(false)
  // 幂等键：打开弹窗时生成一个，同一张弹窗里的重复提交带的是同一个值。
  // 后端据此把第二次请求认成"刚才那份"，不再多建一份带独立编号的草稿。
  const [uploadRequestKey, setUploadRequestKey] = useState('')
  const [approvalTarget, setApprovalTarget] = useState<ContractDocument | null>(null)
  const [originalFile, setOriginalFile] = useState<File | null>(null)
  const [originalFileId, setOriginalFileId] = useState<number | null>(null)
  const [contractTitle, setContractTitle] = useState('')
  const [contractOrigin, setContractOrigin] = useState<'company' | 'customer' | undefined>()
  const [uploadForm, setUploadForm] = useState({
    customer_id: undefined as number | undefined,
    // 正式依据（业务口径：条款可以先备，但登记签署前必须挂上订单或已发出的报价）
    order_id: undefined as number | undefined,
    quote_id: undefined as number | undefined,
    // 合同钉死的报价版本。报价能出 V2/V3，不指定就没法证明金额依据的是哪一版。
    quote_version_id: undefined as number | undefined,
    expiry_date: '',
    effective_date: '',
  })
  // 订单 / 报价 / 报价版本三级联动，全部跟着所选客户走——
  // 不这么做的话，跨客户把别家的单子挂上来，只能等提交时被后端拒掉。
  const genOrdersQuery = useQuery({
    queryKey: ['contract-gen-orders', uploadForm.customer_id],
    queryFn: () => listOrders({ customer_id: uploadForm.customer_id, page: 1, page_size: 50 }),
    enabled: Boolean(uploadForm.customer_id) && uploadVisible,
  })
  const genQuotesQuery = useQuery({
    queryKey: ['contract-gen-quotes', uploadForm.customer_id],
    queryFn: () => listQuotes({ customer_id: uploadForm.customer_id, page: 1, page_size: 50 }),
    enabled: Boolean(uploadForm.customer_id) && uploadVisible,
  })
  const genVersionsQuery = useQuery({
    queryKey: ['contract-gen-quote-versions', uploadForm.quote_id],
    queryFn: () => listQuoteVersions(uploadForm.quote_id!),
    enabled: Boolean(uploadForm.quote_id) && uploadVisible,
  })
  const genOrders = genOrdersQuery.data?.items ?? []
  const genQuotes = genQuotesQuery.data?.items ?? []
  const genVersions = genVersionsQuery.data ?? []
  const selectedOrder = genOrders.find((o) => o.id === uploadForm.order_id)
  const uploadMutation = useMutation({
    mutationFn: async () => {
      if (!uploadForm.customer_id) throw new Error('请选择合同所属客户')
      if (!contractOrigin) throw new Error('请选择合同来源：我方合同或客户合同')
      let sourceId = originalFileId
      if (!originalFile) throw new Error('请上传合同 Word 或 PDF 原文件')
      if (!sourceId) {
        const row = await uploadFile(originalFile)
        sourceId = row.id
        setOriginalFileId(row.id)
      }
      return uploadContractDocument({
        original_file_id: sourceId,
        contract_origin: contractOrigin,
        title: contractTitle.trim() || undefined,
        customer_id: uploadForm.customer_id!,
        order_id: uploadForm.order_id ?? null,
        quote_id: uploadForm.quote_id ?? null,
        quote_version_id: uploadForm.quote_version_id ?? null,
        expiry_date: uploadForm.expiry_date || null,
        effective_date: uploadForm.effective_date || null,
        request_key: uploadRequestKey || undefined,
      })
    },
    onSuccess: (doc) => {
      Toast.success(`合同已上传：${doc.doc_no}`)
      setUploadVisible(false)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const withdrawDingtalkMutation = useMutation({
    mutationFn: (docId: number) => withdrawContractDingTalkApproval(docId),
    onSuccess: () => {
      Toast.success('已撤回钉钉合同签署申请')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const openUploadModal = () => {
    setOriginalFile(null)
    setOriginalFileId(null)
    setContractTitle('')
    setContractOrigin(undefined)
    setUploadForm({ customer_id: undefined, order_id: undefined, quote_id: undefined,
      quote_version_id: undefined, expiry_date: '', effective_date: '' })
    setUploadRequestKey(newRequestKey())
    setUploadVisible(true)
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
  const [searchParams] = useSearchParams()
  const requestedContract = searchParams.get('contract')
  useEffect(() => {
    if (!requestedContract || !/^\d+$/.test(requestedContract)) return
    let active = true
    getContractDocument(Number(requestedContract)).then((doc) => {
      if (active) setDetailTarget(doc)
    }).catch((error: Error) => { if (active) Toast.error(error.message) })
    return () => { active = false }
  }, [requestedContract])
  // 认领弹窗（"结果待人工核对"时的三个出口之一）
  const [adoptTarget, setAdoptTarget] = useState<OaApprovalInstance | null>(null)
  const [adoptKey, setAdoptKey] = useState<string | null>(null)
  const detailQuery = useQuery({
    queryKey: ['contract-detail', detailTarget?.id],
    queryFn: () => getContractDocument(detailTarget!.id),
    enabled: Boolean(detailTarget),
    refetchInterval: 5000,
    refetchOnWindowFocus: true,
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

  // 已选签署件的文件名：确认前要让用户看清"签的是哪份文件"，光给内部编号看不懂
  const selectedSignFile = (signFilesQuery.data ?? []).find((row) => row.id === signFileId)
  const documents = documentsQuery.data?.items ?? []

  const canUploadSignFile = can('file:manage')
  const noFileManageReason =
    '你没有「文件管理」权限，无法上传签署件；请让有该权限的同事上传，或请管理员为你开通后重试。'
  const signBlocked = [
    // 已经选到签署件时不再说权限：这时确认键是**能点**的（从已上传的文件里选一份照样能登记），
    // 说了就变成「明明能签却告诉我还不能」的假话。只有选不出文件时这条才是真原因。
    { ok: canUploadSignFile || Boolean(signFileId), reason: noFileManageReason },
    // 上传中确认键也是灰的：这时说「请先选择或上传签署件」是句假话（文件正在传），要分开说
    { ok: !signUploading, reason: '签署件正在上传，等上传完成后再确认签署。' },
    { ok: Boolean(signFileId) || signUploading, reason: '请先选择或上传要登记的签署件。' },
  ]
  const signBlockedReasons = blockedReasons(signBlocked)
  // ① 看得见（弹窗底部常驻）+ ② 点得动。确认键在弹窗 footer（portal）里，外面包一层收不到点击，
  // 所以照 SampleFromSourceModal 的做法把**那段说明**做成点击落点；上传键就在正文里，
  // 能直接包一层 —— 灰着的时候点它自己也会弹一句同样的话。
  const notifySignBlocked = useBlockedNotify(signBlockedReasons, '登记签署')
  const voidNeedsReviewerReason =
    '已签合同的作废需要复核人（主管）操作；请联系主管处理，或请管理员为你开通复核权限后重试。'
  const notifyVoidBlocked = useBlockedNotify([voidNeedsReviewerReason], '作废')

  return (
    <div className="page-container">
        <SectionCard>
          <div className="toolbar" style={{ marginBottom: 10 }}>
            <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
              我方合同、客户合同均上传原文件；审批在钉钉办理，CRM 同步进度并跟催回签归档
            </span>
            <div style={{ flex: 1 }} />
            {canManage && (
              <Button theme="solid" onClick={() => openUploadModal()}>
                上传合同
              </Button>
            )}
          </div>
          <Table<ContractDocument>
            columns={[
              { title: '编号', dataIndex: 'doc_no', width: 150 },
              { title: '类型', dataIndex: 'doc_type_label', width: 100 },
              { title: '标题', dataIndex: 'title' },
              { title: '合同来源', width: 110, render: (_: unknown, record: ContractDocument) => record.contract_origin_label || '历史未标注' },
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
              // ⚠️ 原来是 `.slice(0, 10)`（UTC 取日）；签署时间是 UTC 瞬时，
              // 北京时间凌晨签的会显示成前一天。
              { title: '签署时间', dataIndex: 'signed_at', width: 130, render: (v: string | null) => (v ? formatBeijingDate(v) : '-') },
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
                    {/* 走共用封装：原件丢失 / 没权限 / 网络失败都要把话说出来，
                        不能 `void` 掉一个会失败的 Promise（那样点了没反应） */}
                    <a onClick={() => void downloadContractDoc(record)}>下载合同原稿</a>
                    {/* 详情：抬头快照、依据、关系链、签署原件都收在这里。
                        签署原件**光看状态看不出来**，必须点进来才知道拿到的到底是哪一份。 */}
                    <a onClick={() => setDetailTarget(record)}>详情</a>
                    {canManage && record.status === 'draft' && (
                      <a onClick={() => openSignModal(record)}>登记签署</a>
                    )}
                    {canManage && record.status === 'draft' && record.source_type === 'customer_drafted' && (
                      record.dingtalk_oa?.status === 'pending' ? (
                        <>
                          <a onClick={() => setDetailTarget(record)}>查看钉钉审批</a>
                          <a
                            onClick={() =>
                              Modal.confirm({
                                title: '撤回钉钉合同签署申请？',
                                content: '撤回只对仍在审批中的钉钉流程生效，已完成的审批不能撤回。',
                                onOk: () => withdrawDingtalkMutation.mutate(record.id),
                              })
                            }
                          >
                            撤回钉钉审批
                          </a>
                        </>
                      ) : (
                        <a onClick={() => setApprovalTarget(record)}>
                          钉钉合同审批
                        </a>
                      )
                    )}
                    {/* 已签合同的作废要复核人（主管）：以前不是复核人时这个入口**整个消失**，
                        用户只看到「作废」不见了，不知道为什么、也不知道该找谁 —— 正是主人
                        2026-10-12 说的那一类。现在灰着并写明原因，点一下还会弹同一句话
                        （见 BlockedHint.tsx）；能不能作废的判据一个字没动。 */}
                    {canManage &&
                      record.status !== 'void' &&
                      (record.status !== 'signed' || isReviewer ? (
                        <a
                          style={{ color: 'var(--crm-danger, #d45)' }}
                          onClick={() => {
                            setVoidTarget(record)
                            setVoidReason('')
                          }}
                        >
                          作废
                        </a>
                      ) : (
                        <span
                          className="blocked-hint-click"
                          style={{ gap: 6, cursor: 'not-allowed' }}
                          onClick={notifyVoidBlocked}
                        >
                          <span style={{ color: 'var(--crm-text-3)', pointerEvents: 'none' }}>作废</span>
                          <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                            需复核人（主管）操作
                          </span>
                        </span>
                      ))}
                  </span>
                ),
              },
            ]}
            scroll={{ x: 'max(100%, 1400px)' }}
            dataSource={documents}
            loading={documentsQuery.isLoading}
            rowKey="id"
            pagination={{
              currentPage: docPage,
              pageSize: 20,
              total: documentsQuery.data?.total ?? 0,
              onPageChange: setDocPage,
            }}
            empty={
              canManage
                ? '还没有合同，点击「上传合同」添加原文件'
                : '还没有合同文档。上传合同需要「订单管理」权限，请联系管理员或合同负责人。'
            }
          />
        </SectionCard>

      <Modal
        title="上传合同"
        visible={uploadVisible}
        onCancel={() => {
          if (uploadMutation.isPending) { Toast.warning('合同正在保存，请稍候'); return }
          setUploadVisible(false)
            }}
        onOk={() => uploadMutation.mutate()}
        confirmLoading={uploadMutation.isPending}
        maskClosable={!uploadMutation.isPending}
        okText="上传合同"
        cancelText="取消"
        width={560}
      >
        <div style={{ display: 'grid', gap: 12 }}>
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
              value={uploadForm.customer_id}
              onChange={(v) =>
                setUploadForm({
                  ...uploadForm,
                  customer_id: v as number,
                  // 换客户必须把依据一起清掉：上一家的订单/报价挂到这一家头上，
                  // 提交时会被后端拒掉，但让用户先选好再被拒更莫名其妙。
                  order_id: undefined,
                  quote_id: undefined,
                  quote_version_id: undefined,
                })
              }
              optionList={optionsWithCodeOnDuplicate(
                customersQuery.data?.items ?? [],
                (c) => c.name,
                (c) => c.id,
              )}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>合同来源（必选）</div>
            <Select
              style={{ width: '100%' }}
              placeholder="选择我方合同或客户合同"
              value={contractOrigin}
              onChange={(v) => setContractOrigin(v as 'company' | 'customer')}
              optionList={[{ value: 'company', label: '我方合同' }, { value: 'customer', label: '客户合同' }]}
            />
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginTop: 4 }}>
              我方合同：使用我方提供的条款版本；客户合同：使用客户提供的条款版本。
            </div>
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>合同名称</div>
            <Input value={contractTitle} onChange={setContractTitle} placeholder="填写合同名称，需与钉钉一致" />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>合同原文件（Word / PDF）</div>
            <input type="file" accept=".pdf,.doc,.docx" onChange={(e) => {
              const file = e.target.files?.[0] || null
              setOriginalFile(file); setOriginalFileId(null)
              if (file && !contractTitle) setContractTitle(file.name.replace(/\.[^.]+$/, ''))
            }} />
            {originalFile && <div>{originalFile.name}</div>}
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>关联订单（可选）</div>
            <Select
              style={{ width: '100%' }}
              placeholder={uploadForm.customer_id ? '这张客户下的正式订单' : '先选客户'}
              filter={optionMatcher}
              disabled={!uploadForm.customer_id}
              loading={genOrdersQuery.isFetching}
              value={uploadForm.order_id}
              onChange={(v) => setUploadForm({ ...uploadForm, order_id: v as number })}
              optionList={genOrders.map((o) => ({
                value: o.id,
                label: `${o.order_no} · ¥${o.total_amount}`,
              }))}
            />
            {selectedOrder?.quote_version_id && (
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginTop: 4 }}>
                这张订单是照某个报价版本转过来的，关联时会自动采用那一版（不用再选）
              </div>
            )}
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>关联报价（可选）</div>
            <Select
              style={{ width: '100%' }}
              placeholder={uploadForm.customer_id ? '这张客户下的报价单' : '先选客户'}
              filter={optionMatcher}
              disabled={!uploadForm.customer_id}
              loading={genQuotesQuery.isFetching}
              value={uploadForm.quote_id}
              onChange={(v) =>
                setUploadForm({
                  ...uploadForm,
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
              placeholder={uploadForm.quote_id ? '选具体版本' : '先选报价单'}
              disabled={!uploadForm.quote_id}
              loading={genVersionsQuery.isFetching}
              value={uploadForm.quote_version_id}
              onChange={(v) => setUploadForm({ ...uploadForm, quote_version_id: v as number })}
              optionList={genVersions.map((v) => ({
                value: v.id,
                label: `V${v.version_no} · ¥${v.total_amount}`,
              }))}
            />
            <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginTop: 4 }}>
              合同会钉死这一版：报价之后出 V2、V3 都不会改动已保存的合同。
              不选也能先备条款，但登记签署前必须补上正式依据。
            </div>
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>合同到期日（可选）</div>
            <DatePicker
              type="date"
              format="yyyy-MM-dd"
              showClear
              style={{ width: '100%' }}
              placeholder="选择到期日（可留空）"
              value={uploadForm.expiry_date ? new Date(uploadForm.expiry_date) : undefined}
              onChange={(_, dateStr) =>
                setUploadForm({ ...uploadForm, expiry_date: (dateStr as string) || '' })
              }
            />
          </div>
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
            {/* 灰按钮要能解释自己：用共用件 `GuardedButton`（2026-10-15 换），
              它内部就是"禁用态 pointerEvents:none + 外层接点击"，页面不用再手写。 */}
            <GuardedButton
              loading={signUploading}
              disabled={!canUploadSignFile}
              blockedReasons={canUploadSignFile ? [] : [noFileManageReason]}
              action="上传"
              onClick={() => signFileInput.current?.click()}
            >
              上传签署件
            </GuardedButton>
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
                ? `已选：${selectedSignFile?.file_name ?? '已选文件'}`
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
          {/* 弹窗底部（页脚正上方）：确认键灰着的时候，原因必须在同一屏里看得见 ——
              上面那个「从已上传的文件里选」下拉在没有文件时根本不渲染，用户可能一眼看不到任何出路。
              这段文字本身也是「点得动」的落点：灰按钮不吃点击，用户自然会点在它旁边这行字上
              （确认键在弹窗 footer 里，包一层收不到点击，做法同 SampleFromSourceModal）。 */}
          {signBlockedReasons.length > 0 && (
            <div
              data-blocked-click="1"
              className="blocked-hint-click"
              style={{ display: 'block' }}
              onClick={notifySignBlocked}
            >
              <BlockedHint reasons={signBlockedReasons} action="登记签署" />
            </div>
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
            作废后不可恢复，已上传或存档的原件仍保留。原因会写进台账和审计，请写清楚真实原因
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
              <div style={DETAIL_LABEL}>抬头（建档时固定，客户/公司后来改名也不影响下载）</div>
              <div style={DETAIL_VALUE}>
                {detail.header_snapshot?.company_name || '（未设置公司抬头）'} · 客户：
                {detail.header_snapshot?.customer_name ?? detail.customer_name ?? '-'}
              </div>
            </div>
            <div>
              <div style={DETAIL_LABEL}>合同来源</div>
              <div style={DETAIL_VALUE}>{detail.contract_origin_label || '历史未标注'}</div>
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
                    ? '没有找到签署原件：登记时挂上的文件可能已被删除或解绑。不能拿未签原稿当签署件用，请向经手人确认原件去向。'
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
                      {row.attached_at ? ` · 登记于 ${formatBeijingDate(row.attached_at)}` : ''}
                    </span>
                  </div>
                ))
              )}
            </div>
            {canManage && detail.status === 'draft' && detail.original_file_id && !detail.order_id && !detail.quote_id && <ContractBasis doc={detail} />}
            {detail.dingtalk_oa && (
              <div>
                <div style={DETAIL_LABEL}>钉钉合同签署审批</div>
                <div style={{ fontSize: 12, marginBottom: 8 }}>页面每5秒自动更新记录，无需刷新浏览器。</div>
                {canManage && detail.dingtalk_oa_row?.instance_id && <SyncOaButton oaId={detail.dingtalk_oa_row.id} invalidateKeys={[[ 'contract-detail',detail.id ],['contract-documents']]}/>}
                <div style={DETAIL_VALUE}>
                  {detail.dingtalk_oa.status === 'approved'
                    ? '钉钉审批已通过，等待上传客户回签文件'
                    : detail.dingtalk_oa.status === 'rejected'
                      ? '钉钉审批已驳回，请查看钉钉处理意见'
                      : detail.dingtalk_oa.status === 'withdrawn'
                        ? '钉钉审批已撤回'
                        : '钉钉审批处理中或需核对，页面将自动更新'}
                  {detail.dingtalk_oa.synced_at
                    ? ` · 最近同步 ${formatBeijingTime(detail.dingtalk_oa.synced_at)}`
                    : ''}
                </div>
                {detail.dingtalk_oa.instance_id && canManage && <Button size="small" onClick={async () => {
                  try {
                    await api.post(`/contract-documents/${detail.id}/approval-sync`, {})
                    void queryClient.invalidateQueries({ queryKey: ['contract-detail', detail.id] })
                    refresh()
                    Toast.success('审批进度已同步')
                  } catch (e) { Toast.error(e instanceof Error ? e.message : '同步失败') }
                }}>刷新钉钉进度</Button>}
                {detail.dingtalk_oa.result && <ApprovalHistory result={detail.dingtalk_oa.result} />}
                {/* 「结果待人工核对」的三个出口。按钮由服务端给的 allowed_actions
                    决定：一条超时的发起可能已经在钉钉建了单，自动重发会多出一张，
                    所以只能人来核对后选认领 / 重发 / 作废。合同这条链原先一个
                    按钮都没有，超时后这份合同彻底卡死。 */}
                {detail.dingtalk_oa_row && (
                  <div style={{ marginTop: 8, display: 'flex', gap: 12 }}>
                    <ResolveOaActions
                      row={detail.dingtalk_oa_row}
                      invalidateKeys={[['contract-detail', detailTarget?.id]]}
                      onAdopt={(target) => {
                        setAdoptTarget(target)
                        setAdoptKey(newRequestKey())
                      }}
                      onDone={() => void detailQuery.refetch()}
                    />
                  </div>
                )}
              </div>
            )}
            {Object.keys(detail.missing_fields ?? {}).length > 0 && (
              <div>
                <div style={DETAIL_LABEL}>历史合同尚未填入的信息</div>
                <div style={{ fontSize: 12, color: 'var(--crm-warning, #d68000)' }}>
                  {Object.entries(detail.missing_fields)
                    .map(([token, reason]) => `${templateFieldLabel(token)}（${reason}）`)
                    .join('；')}
                </div>
              </div>
            )}
          </div>
        )}
      </Modal>
      <CustomerApproval doc={approvalTarget} onClose={() => setApprovalTarget(null)} />
      <AdoptOaInstanceModal
        target={adoptTarget}
        requestKey={adoptKey}
        invalidateKeys={[['contract-detail', detailTarget?.id], ['contract-documents', docPage]]}
        onClose={() => {
          setAdoptTarget(null)
          setAdoptKey(null)
        }}
      />
    </div>
  )
}
