import { formatBeijingDate, formatBeijingTime } from '../../shared/beijing-time'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ApprovalHistory, ContractBasis } from './CustomerApproval'
import { api } from '../../shared/api/client'
import { templateFieldLabel } from './template-fields'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  DatePicker,
  Dropdown,
  Input,
  Modal,
  Select,
  Table,
  Toast,
  TextArea,
} from '@douyinfe/semi-ui'
import { usePermissions } from '../../shared/hooks/permissions'
import PageHeader from '../../shared/components/PageHeader'
import SectionCard from '../../shared/components/SectionCard'
import StatusDot from '../../shared/components/StatusDot'
import type { TagTone } from '../../shared/types'
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
  startContractOaApproval,
  withdrawContractDingTalkApproval,
  voidContractDocument,
  deleteContractDocument,
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
import AutoOaSync from '../../shared/components/AutoOaSync'
import SyncOaButton from '../../shared/components/SyncOaButton'
import { oaSyncErrorMessage, type OaApprovalInstance } from '../../shared/api/dingtalk'

const STATUS_TONE: Record<string, 'green' | 'grey' | 'red'> = {
  signed: 'green',
  draft: 'grey',
  void: 'red',
}

const DETAIL_LABEL = { fontSize: 12, color: 'var(--crm-text-3)', marginBottom: 2 }
const DETAIL_VALUE = { fontSize: 13 }

export default function DocumentsPage() {
  /* 1024 宽内容区约 700px，合同台账九列合计 1123px，整张表被裁在卡片外、
     底部常驻横向滚动条。窄屏切紧凑档：类型并进合同编号第二行，
     客户并进标题第二行，到期日 / 签署时间不在列表里占位，操作列收 3 个链接。 */
  const [compact, setCompact] = useState(() => window.innerWidth < 1200)
  useEffect(() => {
    const onResize = () => setCompact(window.innerWidth < 1200)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const queryClient = useQueryClient()
  const { can, isReviewer } = usePermissions()
  const canManage = can('order:manage')

  // 台账真分页：后端原来写死 limit 500，第 501 份合同在页面上永远不出现、也没有提示
  const [docPage, setDocPage] = useState(1)
  const [docPageSize, setDocPageSize] = useState(20)
  const documentsQuery = useQuery({
    queryKey: ['contract-documents', docPage, docPageSize],
    queryFn: () => listContractDocuments({ page: docPage, page_size: docPageSize }),
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
  const [originalFile, setOriginalFile] = useState<File | null>(null)
  const [originalFileId, setOriginalFileId] = useState<number | null>(null)
  const [contractTitle, setContractTitle] = useState('')
  const [contractNumber, setContractNumber] = useState('')
  const [numberRuleType, setNumberRuleType] = useState<'manual' | 'rule'>('manual')
  const [ourCompany, setOurCompany] = useState('')
  const [counterparty, setCounterparty] = useState('')
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
      if (!contractTitle.trim()) throw new Error('请填写合同名称')
      if (!ourCompany.trim()) throw new Error('请填写我方主体')
      if (!counterparty.trim()) throw new Error('请填写对方主体')
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
        title: contractTitle.trim(),
        our_company: ourCompany.trim(),
        counterparty_name: counterparty.trim(),
        contract_number: numberRuleType === 'manual' ? (contractNumber.trim() || undefined) : undefined,
        contract_number_rule_type: numberRuleType,
        contract_number_rule_fix: numberRuleType === 'rule' ? '系统默认规则' : undefined,
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
      Toast.success('已撤回 OA 合同审批')
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const [oaStartTarget, setOaStartTarget] = useState<ContractDocument | null>(null)
  const startOaMutation = useMutation({
    mutationFn: (vars: { id: number; our_company: string; counterparty_name: string }) =>
      startContractOaApproval(vars.id, {
        our_company: vars.our_company,
        counterparty_name: vars.counterparty_name,
      }),
    onSuccess: () => {
      Toast.success('已提交 OA 合同审批')
      setOaStartTarget(null)
      refresh()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const openOaStart = (record: ContractDocument) => {
    setOaStartTarget(record)
  }

  const submitOaStart = () => {
    if (!oaStartTarget) return
    // 主体在上传合同时已填并落库，这里不再让用户确认/修改
    const our = (oaStartTarget.our_company ?? '').trim()
    const other = (oaStartTarget.counterparty_name ?? '').trim()
    if (!our || !other) {
      Toast.warning('这份合同上传时没有填写主体，请重新上传或联系管理员补齐')
      return
    }
    startOaMutation.mutate({ id: oaStartTarget.id, our_company: our, counterparty_name: other })
  }

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
  /** 合同原文件的隐藏输入框：可见触发器是按钮，不再露出浏览器原生控件 */
  const originalFileInput = useRef<HTMLInputElement>(null)
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
  const oaLabel = detail?.dingtalk_oa?.provider === 'self_oa' ? 'OA' : '同序 OA'
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
  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteContractDocument(id),
    onSuccess: () => { Toast.success('合同已移入回收站'); refresh(); void queryClient.invalidateQueries({ queryKey: ['recycle', 'contracts'] }) },
    onError: (error: Error) => Toast.error(error.message),
  })

  // 已选签署件的文件名：确认前要让用户看清"签的是哪份文件"，光给内部编号看不懂
  const selectedSignFile = (signFilesQuery.data ?? []).find((row) => row.id === signFileId)
  const documents = documentsQuery.data?.items ?? []
  /* 「到期日」这一列在真实数据里几乎是空的（草稿合同一律没有到期日）。
     空着的 80px 一直占着首屏，把「标题」挤到只剩 12 个字、整列省略号。
     有值才出这一列，没值就把宽度还给标题 —— 而不是固定留一列横杠。 */
  const hasExpiry = documents.some((row) => Boolean(row.expiry_date))

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
        {/* 原来直接从卡片开始，没有标题区；其他模块都有「XX 中心 + 一句说明」，
            切过来会觉得少了一块（2026-11-16 巡检）。 */}
        <PageHeader
          title="文档管理"
          subtitle="我方合同、客户合同均上传原文件；审批在同序 OA 办理，CRM 同步进度并跟催回签归档"
          /* 2026-11-20：原来「上传合同」单独占卡片内左上角一行，右边一整排空白，
             和其它列表页"新建类按钮在页头右上"（案例库「新建案例」）也不一致。
             挪到页头 extra，卡片从表格直接开始。 */
          extra={canManage && (
            <Button type="primary" theme="outline" onClick={() => openUploadModal()}>
              上传合同
            </Button>
          )}
        />
        <SectionCard
          className={(documents?.length ?? 0) <= 8 ? 'auto-height' : undefined}
        >
          <Table<ContractDocument> className="table-center documents-table"
            columns={[
              // 140 放不下 12 位的合同编号（实测被截成「CT202610140…」）。
              // 156 是刚好够 + 又不把表格撑出横向滚动条的宽度。
              // 类型并到编号下面第二行（2026-11-19，原宽屏档有独立「类型」列 85px）：
              // 九列加起来 1118、容器约 1130，标题只分到 120（约 7 个字），
              // 15 字的「【全场景演示】销售合同-甲」整列都是省略号。
              // 把类型降级成 11px 小灰字挂在编号下，空出的 85px 全给标题（120→205）。
              // 不丢信息、不多行高（客户列本来就是两行），宽屏窄屏从此同一套读法。
              {
                title: '合同编号',
                dataIndex: 'doc_no',
                /* 148 刚够 14 位的 CT202611180001（13px 约 133），原来 156 多给了 8px。 */
                width: compact ? 128 : 148,
                render: (v: string, record: ContractDocument) => (
                  <div>
                    <div>{v}</div>
                    <div style={{ marginTop: 2, fontSize: 11, color: 'var(--crm-text-3)' }}>
                      {record.doc_type_label ?? '-'}
                    </div>
                  </div>
                ),
              },
              // 标题不给上限时它会吃掉所有剩余宽度（实测 270px），
              // 把后面的列全顶到屏幕外；205 是"销售合同-甲"这类标题的实测
              // 上限（约 12–13 字）再留些余量。
              {
                title: '标题',
                dataIndex: 'title',
                /* 205 只够 12–13 个字；「【验收】非正式合同 202611180001」被截成
                   「【验收】非正式合同 20261…」。没有到期日列时把省下的 80px 给它。 */
                width: compact ? 160 : hasExpiry ? 190 : 288,
                // 标题超过列宽会被省略成「【全场景演示】销售…」，悬停要看得到全文
                // （2026-11-17 视觉审视 V-03）。同时给 title 属性，键盘/读屏也能读到。
                render: (v: string, record: ContractDocument) => (
                  <div style={{ minWidth: 0 }}>
                    <span
                      title={v}
                      className="cell-strong"
                      style={{
                        display: 'block',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {v}
                    </span>
                    {compact && (
                      <div
                        title={record.customer_name ?? '-'}
                        style={{
                          marginTop: 2,
                          fontSize: 11,
                          color: 'var(--crm-text-3)',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {record.customer_name ?? '-'}
                      </div>
                    )}
                  </div>
                ),
              },
              ...(compact
                ? []
                : [// 客户名是台账的识别维度：原来 95px 只够放五个字，
                // 满屏「青岛包装…」「【全场景…」，一列全是省略号等于没有客户列。
                // 从标题（160→120）和到期日（90→80）各让一点，整表总宽不变。
                {
                  title: '客户',
                  dataIndex: 'customer_name',
                  width: 116,
                  // 140px 只放得下七八个字，「【全场景演示】华东复购采购客户」这
                  // 类名字照样截。改成最多两行：一行装不下的往下走，行的总高
                  // 只多一行，比整列显示成「【全场景演示】…」有用得多。
                  render: (v: string | null) => (
                    <span
                      title={v ?? ''}
                      style={{ display: 'block', whiteSpace: 'normal', lineHeight: 1.45 }}
                    >
                      {v || '-'}
                    </span>
                  ),
                }]),
              {
                title: '状态',
                dataIndex: 'status_label',
                /* 88 会把「待签草稿」压成「待签草稿…」：四个字 ≈56 + 圆点 8 + 间距 6 + 内边距 24。 */
                width: compact ? 96 : 96,
                render: (v: string, record: ContractDocument) => (
                  <StatusDot label={v} tone={(STATUS_TONE[record.status] ?? 'grey') as TagTone} />
                ),
              },
              {
                // 「依据」= 这份合同是照哪一版报价 / 哪张订单签的。
                // 报价能出 V2/V3，不写清版本，台账上光看单号证明不了金额依据。
                title: compact ? '依据' : '依据 / 来源',
                /* 130：13 位的 SO202611180001（13px 约 101）+ 内边距 24 = 125，刚好放下；
                   再宽就是白占，不够就是「Q202610120001 …」。 */
                /* 134 时实测「SO202611180001」还是被压成「SO2026111800…」（2026-11-20 截图），
                   14 位单号比注释里估的 13 位长一位。让 15px 给「标题」，单号就不省略了。 */
                width: compact ? 170 : 149,
                render: (_: unknown, record: ContractDocument) => {
                  const header = record.header_snapshot
                  const basis = record.quote_version_no
                    ? `${header?.quote_no ?? '报价'} V${record.quote_version_no}`
                    : header?.order_no
                      ? `订单 ${header.order_no}`
                      : header?.quote_no
                        ? `报价 ${header.quote_no}`
                        : ''
                  // 「依据」和「合同来源」合成一列：前者是照哪版报价签的，
                  // 后者是我方合同 / 客户合同，都属"这份合同从哪来"。
                  // 两列各占 170 / 105，加起来把整表顶出卡片；合成一列后
                  // 来源退成第二行小灰字，一屏就塞得下了，也省得横向拖。
                  return (
                    <div style={{ display: 'grid', gap: 2 }}>
                      {basis ? (
                        <span
                          title={basis}
                          /* 窄屏 170px 放不下「订单 + 15 位单号」：去掉前缀，
                             单号本身 SO / Q 开头就能认出是订单还是报价 */
                          data-compact={compact ? '1' : undefined}
                          style={{
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {/* 宽屏也去掉「订单 / 报价」前缀：SO / Q 开头本来就认得出，
                              5 个字的前缀换 5 个字的单号，150px 才放得下完整单号。 */}
                          {basis.replace(/^(订单|报价) /, '')}
                        </span>
                      ) : (
                        <span style={{ color: 'var(--crm-text-3)' }}>未绑定</span>
                      )}
                      <span style={{ fontSize: 11, color: 'var(--crm-text-3)' }}>
                        {record.contract_origin_label || '来源未标注'}
                      </span>
                    </div>
                  )
                },
              },
              // 「合同来源」从第 4 位挪到「依据」后面：它是"我方合同 / 客户合同"，
              // 实际数据里满屏都是「历史未标注」，占着黄金位置却没信息；
              // 让位给「依据」——那份合同到底照哪一版报价签的，才是要一眼看到的。
              // 到期日 / 签署时间是两个**经常为空**的列（草稿合同一律是横杠），
              // 却排在首屏位置上，把真正有内容的「依据」挤到了滚动区。
              // 有值的往前、空着的往后 —— 用户不用横滑就能看见这段合同照哪版报价签的。
              /* 整列都是 "-" 的列不上（见上面 hasExpiry）：一排横杠不是信息，
                 还占着首屏最值钱的一段宽度。有一条带到期日就正常显示。 */
              ...(compact || !hasExpiry
                ? []
                /* 92 时实测被压成「2027-0…」；和签署时间同一个日期格式，
                   108 才是"不省略"的线。 */
                : [{ title: '到期日', dataIndex: 'expiry_date', width: 108, render: (v: string | null) => v ?? '-' }]),
              // ⚠️ 原来是 `.slice(0, 10)`（UTC 取日）；签署时间是 UTC 瞬时，
              // 北京时间凌晨签的会显示成前一天。
              ...(compact
                ? []
                /* 100 时实测被 Semi 压成「2026-11…」：2026-11-18 是 10 个字符 ≈ 96px，
                   108 才是"不省略"的线。 */
                : [{ title: '签署时间', dataIndex: 'signed_at', width: 108, render: (v: string | null) => (v ? formatBeijingDate(v) : '-') }]),
              {
                // 「下载」不跟管理操作挤在一起：下载接口要的是 order:view，
                // 原来整列挂在 canManage 下，只读用户连下载入口都看不见。
                title: '操作',
                // 九个列宽加起来 1146，比内容区（1130）多 16，挤出横向滚动条。
                // 从「到期日」和「操作」各让一点：到期日 90 够放 2026-11-16。
                // 165 不变：加宽会把整张表撑出横向滚动条（2026-11-17 实拍到 200 就滚了）。
                // 所以这里靠"每个词只占该占的字数"来省地方：下载、签署都是两个字的动作词。
                /* 三个动作词（详情 / 下载 / 更多）实测 140，142 留一点余量。 */
                width: compact ? 144 : 142,
                // 这一列原来是 260 宽、还排在最后：整张表 1400、容器只有 1130，
                // 结果「下载 / 详情 / 登记签署」全被推到屏幕外，必须横向拖才能点 ——
                // 高频操作用户最不该找不着。固定在右侧，横滑时它始终在。
                // 170 是按"下载合同原稿 + 详情"两个链接实测的，少了会变省略号。
                fixed: 'right',
                // 操作列收敛（2026-11-17 视觉审视 D-01）：原来最多 7 个文字链接挤在
                // 165px 里，竖成 4 行、整行被撑高，看着像一列乱码。现在分三层：
                //   ① 详情 / 下载原稿       —— 谁都要用的两个
                //   ② 当前阶段该做的那一个   —— 草稿态是「登记签署」，OA 审批中是「查看 OA 审批」
                //   ③ 「更多」下拉          —— 撤回 OA / 发起 OA / 作废 / 删除这些长尾
                // 判据一个没动：能不能点、点了会失败还是成功，仍走原来的权限与状态判断；
                // 只是把"入口摆在哪"改了。作废被复核人挡住时，条目仍给，点一下弹原因。
                render: (_: unknown, record: ContractDocument) => {
                  const oaPending = Boolean(record.dingtalk_oa && record.dingtalk_oa.status === 'pending')
                  const stageAction =
                    canManage && record.status === 'draft'
                      ? oaPending
                        ? { label: '看 OA', onClick: () => setDetailTarget(record) }
                        : { label: '签署', onClick: () => openSignModal(record) }
                      : null
                  // 主操作只能有一个（2026-11-17 复验 D-01 收尾）：
                  // 草稿态是「签署 / 看 OA」，其余状态（已签、作废）就是「下载」——
                  // 那一行的下一步动作本来就是把这单原件发出去。
                  const primaryAction = stageAction ?? {
                    label: '下载',
                    onClick: () => void downloadContractDoc(record),
                  }
                  // 「下载」不做主操作时退到「更多」里，仍是同一个入口、同一个权限判断，
                  // 只是不再跟主操作抢同一行的视觉重量。
                  const downloadInMore = Boolean(stageAction)

                  const more: { key: string; label: string; onClick?: () => void; danger?: boolean }[] = []

                  if (downloadInMore) {
                    more.push({ key: 'download', label: '下载原件', onClick: () => void downloadContractDoc(record) })
                  }

                  if (canManage && record.status === 'draft') {
                    if (oaPending) {
                      more.push({
                        key: 'withdraw-oa',
                        label: '撤回 OA 审批',
                        onClick: () =>
                          Modal.confirm({
                            title: '撤回 OA 合同审批？',
                            content: '撤回只对仍在审批中的 OA 流程生效，已完成的审批不能撤回。',
                            onOk: () => withdrawDingtalkMutation.mutate(record.id),
                          }),
                      })
                    } else if (record.dingtalk_oa_row?.provider !== 'dingtalk') {
                      more.push({ key: 'start-oa', label: '发起 OA 审批', onClick: () => openOaStart(record) })
                    } else {
                      // 只读的历史同序 OA 审批：给一行说明，不占主位
                      more.push({ key: 'oa-readonly', label: '历史同序 OA 审批（只读，不能重提）' })
                    }
                  }

                  if (canManage && record.status !== 'void') {
                    if (record.status !== 'signed' || isReviewer) {
                      more.push({
                        key: 'void',
                        danger: true,
                        label: '作废',
                        onClick: () => {
                          setVoidTarget(record)
                          setVoidReason('')
                        },
                      })
                    } else {
                      // 不是复核人：条目照样给，点一下把原因说出来（不静默消失）
                      more.push({ key: 'void-blocked', label: '作废（需复核人操作）', onClick: notifyVoidBlocked })
                    }
                  }

                  if (canManage && (record.status === 'draft' || record.status === 'void')) {
                    more.push({
                      key: 'delete',
                      danger: true,
                      label: '删除',
                      onClick: () =>
                        Modal.confirm({
                          title: `删除合同 ${record.doc_no}？`,
                          content: '合同将移入回收站，可在"回收站 → 合同"中恢复。',
                          okButtonProps: { type: 'danger' },
                          onOk: () => deleteMutation.mutate(record.id),
                        }),
                    })
                  }

                  return (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, whiteSpace: 'nowrap', fontSize: 13, paddingRight: 2 }}>
                      <a onClick={() => setDetailTarget(record)}>详情</a>
                      {/* 收敛后只剩三样：详情（看）、一个主操作（做）、更多（长尾）。
                          原来「下载」和阶段动作并排都是蓝的，看不出这行该干什么。 */}
                      <a
                        title={
                          primaryAction.label === '签署'
                            ? '登记签署（填签署日期与原件）'
                            : primaryAction.label === '看 OA'
                              ? '查看 OA 审批进度'
                              : '下载合同原件'
                        }
                        style={{ color: 'var(--crm-primary)', fontWeight: 600 }}
                        onClick={primaryAction.onClick}
                      >
                        {primaryAction.label}
                      </a>
                      {more.length > 0 && (
                        <Dropdown
                          trigger="click"
                          position="bottomRight"
                          clickToHide
                          render={
                            <Dropdown.Menu>
                              {more.map((item) => (
                                <Dropdown.Item
                                  key={item.key}
                                  onClick={item.onClick ?? (() => Toast.info(item.label))}
                                >
                                  <span style={{ color: item.danger ? 'var(--crm-danger, #d45)' : undefined }}>
                                    {item.label}
                                  </span>
                                </Dropdown.Item>
                              ))}
                            </Dropdown.Menu>
                          }
                        >
                          <a style={{ color: 'var(--crm-text-2)' }}>更多</a>
                        </Dropdown>
                      )}
                    </span>
                  )
                },
              },
            ]}
            // 原来这里是 scroll={{ x: 'max(100%, 1400px)' }} —— 把表格最小宽硬锁在 1400，
            // 于是无论怎么调列宽都还是 1400、永远留着横向滚动条（容器只有 1130）。
            // Semi 对这类字符串写法会直接当 min-width 用。现在列宽和约 1110 < 1130，
            // 去掉它，表格就跟着容器走，一屏看完不用横滑。
            // 但少了 scroll 参数后 Semi 会按列宽合计撑开（实测 1146 > 容器 1130），
            // 所以补一条 x: '100%' 明确要求「宽度跟容器」，列多时按比例压。
            scroll={{ x: '100%' }}
            dataSource={documents}
            loading={documentsQuery.isLoading}
            rowKey="id"
            pagination={(documentsQuery.data?.total ?? 0) <= docPageSize ? false : {
              currentPage: docPage,
              pageSize: docPageSize,
              total: documentsQuery.data?.total ?? 0,
              onPageChange: setDocPage,
              showSizeChanger: true,
              pageSizeOpts: [10, 20, 50, 100],
              onPageSizeChange: (size: number) => { setDocPageSize(size); setDocPage(1) },
            }}
            empty={
              canManage
                ? '还没有合同，点击「上传合同」添加原文件'
                : '还没有合同文档。上传合同需要「订单管理」权限，请联系管理员或合同负责人。'
            }
          />
        {(documentsQuery.data?.total ?? 0) <= docPageSize && (
          <div style={{ marginTop: 10, textAlign: 'right', fontSize: 12, color: 'var(--crm-text-3)' }}>
            共 {documentsQuery.data?.total ?? 0} 条
          </div>
        )}
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
        okButtonProps={{ disabled: !contractTitle.trim() || !ourCompany.trim() || !counterparty.trim() }}
        width={720}
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <div>
            <div style={{ marginBottom: 4 }}>客户</div>
            <Select
              style={{ width: '100%' }}
              placeholder="搜索客户名称"
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
            <div style={{ marginBottom: 4 }}>合同名称<span style={{ color: '#f56c6c', marginLeft: 2 }}>*</span></div>
            <Input value={contractTitle} onChange={setContractTitle} placeholder="填写合同名称，需与同序 OA 一致" />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <div style={{ marginBottom: 4 }}>我方主体<span style={{ color: '#f56c6c', marginLeft: 2 }}>*</span></div>
              <Input value={ourCompany} onChange={setOurCompany} placeholder="请填写我方签约主体全称" />
            </div>
            <div>
              <div style={{ marginBottom: 4 }}>对方主体<span style={{ color: '#f56c6c', marginLeft: 2 }}>*</span></div>
              <Input value={counterparty} onChange={setCounterparty} placeholder="请填写对方签约主体全称" />
            </div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <div style={{ marginBottom: 4 }}>合同编号生成方式<span style={{ color: '#f56c6c', marginLeft: 2 }}>*</span></div>
              <Select
                value={numberRuleType}
                onChange={(v) => setNumberRuleType(v as 'manual' | 'rule')}
                optionList={[
                  { value: 'manual', label: '手动填写' },
                  { value: 'rule', label: '选择已有合同编号规则' },
                ]}
              />
            </div>
            <div>
              {numberRuleType === 'manual' ? (
                <div>
                  <div style={{ marginBottom: 4 }}>合同编号</div>
                  <Input
                    value={contractNumber}
                    onChange={setContractNumber}
                    placeholder="选填；留空则使用 CRM 自动生成的编号"
                  />
                </div>
              ) : (
                <div>
                  <div style={{ marginBottom: 4 }}>合同编号规则<span style={{ color: '#f56c6c', marginLeft: 2 }}>*</span></div>
                  <Input value="系统默认规则" readOnly placeholder="系统默认规则" />
                  <div style={{ fontSize: 12, color: '#8a94a6', marginTop: 4 }}>
                    系统将根据所选规则自动生成合同编号
                  </div>
                </div>
              )}
            </div>
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>合同原文件（Word / PDF）</div>
            {/* 原来直接摆一个浏览器原生「选择文件 / 未选择任何文件」，
                和同页 Semi 控件不是一个物种（2026-11-17 视觉审视 V2-12）。
                改成和「上传签署件」同一套：自定义按钮 + 隐藏输入框，
                系统文件选择窗口仍然是浏览器原生的（这层该原生就原生）。 */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <Button onClick={() => originalFileInput.current?.click()}>选择文件</Button>
              <span style={{ fontSize: 12, color: 'var(--crm-text-3)' }}>
                {originalFile ? originalFile.name : '尚未选择文件（支持 .pdf / .doc / .docx）'}
              </span>
              {originalFile && (
                <a
                  onClick={() => {
                    setOriginalFile(null)
                    setOriginalFileId(null)
                    if (originalFileInput.current) originalFileInput.current.value = ''
                  }}
                >
                  清除
                </a>
              )}
            </div>
            <input
              ref={originalFileInput}
              type="file"
              accept=".pdf,.doc,.docx"
              style={{ display: 'none' }}
              onChange={(e) => {
                const file = e.target.files?.[0] || null
                setOriginalFile(file); setOriginalFileId(null)
                if (file && !contractTitle) setContractTitle(file.name.replace(/\.[^.]+$/, ''))
              }}
            />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
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
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
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
        </div>
      </Modal>

      <Modal
        width={520}
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
        width={520}
        title={`发起 OA 合同审批：${oaStartTarget?.doc_no ?? ''}`}
        visible={Boolean(oaStartTarget)}
        onCancel={() => setOaStartTarget(null)}
        onOk={submitOaStart}
        confirmLoading={startOaMutation.isPending}
        okText="提交 OA 审批"
        cancelText="取消"
      >
        <div style={{ display: 'grid', gap: 10 }}>
        </div>
      </Modal>

      <Modal
        width={520}
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
                <div style={DETAIL_LABEL}>{oaLabel} 合同审批</div>
                {/* 5 秒那次只能读 CRM 里存着的快照 —— OA 那边批了，CRM 不动它也永远是 pending。
                    这里挂 AutoOaSync：打开详情先对一次 OA，仍在审批中时每 10 秒对一次，
                    拿到"已通过"就立刻回写并刷新本页，用户不必自己去点"立即同步"。
                    只在 self_oa + pending 时启用，权限沿用页面的 canManage。 */}
                <AutoOaSync
                  enabled={canManage}
                  row={detail.dingtalk_oa_row}
                  invalidateKeys={[['contract-detail', detail.id], ['contract-documents']]}
                />
                <div style={{ fontSize: 12, marginBottom: 8 }}>页面每5秒自动更新记录，无需刷新浏览器。</div>
                {canManage && detail.dingtalk_oa_row?.instance_id && <SyncOaButton oaId={detail.dingtalk_oa_row.id} invalidateKeys={[[ 'contract-detail',detail.id ],['contract-documents']]}/>}
                <div style={DETAIL_VALUE}>
                  {detail.dingtalk_oa.status === 'approved'
                    ? `${oaLabel} 审批已通过，等待上传客户回签文件`
                    : detail.dingtalk_oa.status === 'rejected'
                      ? `${oaLabel} 审批已驳回，请查看处理意见`
                      : detail.dingtalk_oa.status === 'withdrawn'
                        ? `${oaLabel} 审批已撤回`
                        : `${oaLabel} 审批处理中或需核对，页面将自动更新`}
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
                  } catch (e) { Toast.error(oaSyncErrorMessage(e)) }
                }}>刷新{oaLabel}进度</Button>}
                {detail.dingtalk_oa.provider !== 'self_oa' && detail.dingtalk_oa.result && <ApprovalHistory result={detail.dingtalk_oa.result} />}
                {detail.dingtalk_oa.provider !== 'self_oa' && detail.dingtalk_oa_row && (
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
