/**
 * F05 合同/单据/审批页静态证伪脚本。
 * 用法：node review/repro/f05_frontend/check_f05.mjs
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const ROOT = resolve(import.meta.dirname, '../../..')
const F = {
  docs: 'frontend/src/modules/contract/DocumentsPage.tsx',
  capproval: 'frontend/src/modules/contract/CustomerApproval.tsx',
  oaform: 'frontend/src/modules/contract/ContractOaForm.tsx',
  contractApi: 'frontend/src/shared/api/contract.ts',
  bizdoc: 'frontend/src/shared/components/BizDocPanel.tsx',
  contractDocs: 'frontend/src/shared/components/ContractDocsPanel.tsx',
  attach: 'frontend/src/modules/common/AttachmentPanel.tsx',
  gallery: 'frontend/src/shared/components/ImageGallery.tsx',
  fileApi: 'frontend/src/shared/api/file.ts',
  approval: 'frontend/src/modules/approval/ApprovalPage.tsx',
  rules: 'frontend/src/modules/approval/RulesPanel.tsx',
  approvalApi: 'frontend/src/shared/api/approval.ts',
  contractSvc: 'backend/app/modules/contract/service.py',
  fileModel: 'backend/app/modules/file/model.py',
}
const src = Object.fromEntries(Object.entries(F).map(([k, rel]) => [k, readFileSync(resolve(ROOT, rel), 'utf8')]))
const results = []
const check = (id, name, verdict, condition, evidence) =>
  results.push({ id, name, verdict, ok: Boolean(condition), evidence })
function between(text, start, end) {
  const i = text.indexOf(start)
  if (i < 0) return ''
  const j = text.indexOf(end, i + start.length)
  return j < 0 ? text.slice(i) : text.slice(i, j)
}

check('F05-01', '签署件上传即挂 signed，确认再挂一次', 'CONFIRMED',
  /category: 'signed'/.test(src.docs)
    && /if \(!signTarget\) return[\s\S]*?uploadFile\(file, \{[\s\S]*?category: 'signed'/.test(src.docs)
    && /BusinessFile\([\s\S]*?category="signed"/.test(src.contractSvc)
    && !/BusinessFile\.file_id == file_id[\s\S]{0,80}category == "signed"/.test(src.contractSvc),
  '上传就挂 signed；sign_document 无同 file_id 去重；list_signed_files 不看 doc.status',
)

check('F05-02', '合同上传键关闭重开即换', 'CONFIRMED',
  /setUploadRequestKey\(newRequestKey\(\)\)/.test(src.docs)
    && /request_key: uploadRequestKey/.test(src.docs)
    && /onCancel=\{\(\) => \{[\s\S]*?setUploadVisible\(false\)/.test(src.docs)
    && !/setUploadRequestKey\(''/.test(between(src.docs, 'onCancel={() => {', 'setUploadVisible(false)')),
  'openUploadModal 每次换键；取消只关弹窗，不保留上次结果未知的键',
)

check('F05-03', 'BizDocPanel 请求键不随目标重置', 'CONFIRMED',
  /const requestKeyRef = useRef<string \| null>\(null\)/.test(src.bizdoc)
    && /if \(!requestKeyRef\.current\) requestKeyRef\.current = newRequestKey\(\)/.test(src.bizdoc)
    && !/useEffect\([\s\S]{0,120}requestKeyRef/.test(src.bizdoc)
    && /sampleRequestId \?\? orderId \?\? quoteId/.test(src.bizdoc),
  '组件不卸载、目标 id 变了也不清 key；后端同键不同 payload 返回 409',
)

check('F05-04', '审批规则新建/更新无请求键', 'CONFIRMED',
  /draft\.id \? updateApprovalRule\(draft\.id, payload\) : createApprovalRule\(payload\)/.test(src.rules)
    && !/request_key/.test(between(src.approvalApi, 'export function createApprovalRule', 'export function updateApprovalRule'))
    && !/request_key/.test(between(src.approvalApi, 'export function updateApprovalRule', 'export function publishApprovalRule')),
  '两个保存入口都没有 request_key，超时重试会多一条同内容草稿',
)

check('F05-05', '合同/审批/沙盒金额走 number', 'CONFIRMED',
  /`\$\{o\.order_no\} · ¥\$\{o\.total_amount\}`/.test(src.docs)
    && /`V\$\{v\.version_no\} · ¥\$\{v\.total_amount\}`/.test(src.docs)
    && /formatMoney\(toTwoDecimals\(v\), 'CNY'\)/.test(src.approval)
    && /Number\(v\.total_amount\)\.toLocaleString/.test(src.rules)
    && !/total_amount_exact/.test(src.docs),
  '合同下拉直接插值 float；审批 toTwoDecimals(number)；沙盒 Number(...)',
)

check('F05-06', '合同台账/依赖下拉失败显示空', 'CONFIRMED',
  /dataSource=\{documents\}/.test(src.docs)
    && !/documentsQuery\.isError/.test(src.docs)
    && /还没有合同/.test(src.docs),
  '列表只判 isLoading，失败时 items 空；客户/订单/报价/版本下拉无错误态',
)

check('F05-07', 'ContractDocsPanel/BizDocPanel 列表失败显示空态', 'CONFIRMED',
  /const rows = query\.data\?\.items \?\? \[\]/.test(src.contractDocs)
    && !/query\.isError/.test(src.contractDocs)
    && /const compactEmpty = compact && !listQuery\.isLoading && rows\.length === 0/.test(src.bizdoc)
    && !/listQuery\.isError/.test(src.bizdoc),
  '两个组件都只有 loading/rows.length，没有 isError',
)

check('F05-08', '附件/图片列表失败显示没有', 'CONFIRMED',
  /const query = useQuery\(\{[\s\S]*?listBusinessFiles/.test(src.attach)
    && !/query\.isError/.test(src.attach)
    && /visibleFiles\.length === 0 && !query\.isLoading/.test(src.attach)
    && !/query\.isError/.test(src.gallery)
    && /images\.length === 0/.test(src.gallery),
  '两个组件失败都落空数组，直接渲染空态引导',
)

check('F05-09', '大图切换失败仍显示上一张', 'CONFIRMED',
  /setViewerLoading\(true\)/.test(src.gallery)
    && /catch \(error\) \{[\s\S]*?Toast\.error/.test(src.gallery)
    && !/catch \(error\) \{[\s\S]{0,80}setViewerUrl\(null\)/.test(src.gallery)
    && /viewerUrl && !viewerLoading/.test(src.gallery),
  'viewerUrl 只在成功时替换，catch 不清空；finally 后旧图重新渲染',
)

check('F05-10', '规则列表/版本历史/沙盒报价失败显示空（沙盒版本已修）', 'CONFIRMED',
  /const rulesQuery = useQuery\(\{ queryKey: \['approval-rules'\]/.test(src.rules)
    && !/rulesQuery\.isError/.test(src.rules)
    && /还没有规则/.test(src.rules)
    && /还没有发布过版本/.test(src.rules)
    && !/versionsQuery\.isError/.test(between(src.rules, 'function VersionDrawer', 'function SandboxPanel'))
    && !/quotesQuery\.isError/.test(src.rules),
  '规则表/版本历史/沙盒报价仍只有 data/loading；沙盒版本已有 versionsFailed，是部分修复',
)

check('F05-11', '上传合同按钮禁用缺客户/来源/原文件', 'CONFIRMED',
  /okButtonProps=\{\{ disabled: !contractTitle\.trim\(\) \|\| !ourCompany\.trim\(\) \|\| !counterparty\.trim\(\) \}\}/.test(src.docs)
    && /请选择合同所属客户/.test(src.docs)
    && /请选择合同来源/.test(src.docs),
  '按钮不判 customer_id/contractOrigin/originalFile，mutation 内才抛 Toast',
)

check('F05-12', '签署/作废/删除/OA 无请求键', 'CONFIRMED',
  /export function signContractDocument\(docId: number, payload: \{ file_id: number; note\?: string \| null \}\)/.test(src.contractApi)
    && /export function deleteContractDocument\(docId: number\)/.test(src.contractApi)
    && /export function withdrawContractDingTalkApproval\(docId: number\)/.test(src.contractApi)
    && !/request_key/.test(between(src.contractApi, 'export function signContractDocument', 'export function getContractDocument')),
  '这些关键动作 API 都没有 request_key 参数',
)

check('F05-13', '详情 5 秒轮询/错误原文/当前页到期日/伪规则', 'CONFIRMED',
  /refetchInterval: 5000/.test(src.docs)
    && /const hasExpiry = documents\.some/.test(src.docs)
    && /<Input value="系统默认规则" readOnly/.test(src.docs)
    && /Toast\.error\(error\.message\)/.test(src.docs),
  '轮询常开；hasExpiry 只看当前页；规则下拉只有只读“系统默认规则”；错误原文上屏',
)

check('F05-14', '拒绝可空、转交错误原文、审批动作无键', 'CONFIRMED',
  /onOk=\{\(\) => rejectMutation\.mutate\(\)\}/.test(src.approval)
    && !/disabled=\{!rejectComment\.trim\(\)\}/.test(src.approval)
    && /\{usersQuery\.error\.message\}/.test(src.approval)
    && !/request_key/.test(src.approval),
  '拒绝弹窗没有必填门禁；候选人错误原文；审批动作无请求键',
)

check('F05-15', 'BizDoc 作废固定原因、目标缺失仍可生成', 'CONFIRMED',
  /voidBizDoc\(id, '业务作废'\)/.test(src.bizdoc)
    && /generateSampleDoc\(sampleRequestId as number/.test(src.bizdoc)
    && !/disabled=\{!sampleRequestId && !orderId && !quoteVersionId\}/.test(src.bizdoc),
  '作废原因硬编码；生成按钮只看 canManage，不看目标 id 是否存在',
)

check('F05-16', '附件上传/改名/删除无键，无唯一约束', 'CONFIRMED',
  /await uploadFile\(file, \{/.test(src.attach)
    && !/request_key/.test(src.attach)
    && !/UniqueConstraint/.test(between(src.fileModel, 'class BusinessFile', 'class ')),
  'AttachmentPanel 直接 uploadFile；BusinessFile 只有普通索引，同文件可重复挂',
)

check('F05-17', '图片批量上传中途失败留部分且不刷新', 'CONFIRMED',
  /for \(const file of list\) \{[\s\S]*?await uploadFile\(file/.test(src.gallery)
    && /catch \(error\) \{[\s\S]{0,80}Toast\.error/.test(src.gallery)
    && !/catch \(error\) \{[\s\S]{0,120}await refresh\(\)/.test(src.gallery),
  '逐张 await，成功后才统一 refresh；第 N 张失败时前 N-1 张已挂但界面不显示',
)

check('F05-18', 'ContractOaForm initialized 不随 docId、附件部分成功留孤儿', 'CONFIRMED',
  /const initialized = useRef\(false\)/.test(src.oaform)
  && /if \(!query\.data \|\| initialized\.current\) return/.test(src.oaform)
    && !/useEffect\([\s\S]{0,120}\[.*docId/.test(src.oaform)
    && /await Promise\.all\(files\.map\(file => uploadFile/.test(src.oaform)
    && /<ContractOaForm/.test(src.docs),
  '组件已在 DocumentsPage:1175 接线；initialized 只置 true 一次，docId 变化不重置；Promise.all 部分成功不回填',
)

check('F05-19', 'CustomerApproval 未接线/10 秒轮询/原文/无键', 'CONFIRMED',
  /window\.setInterval\(\(\) => autoLink\.mutate\(\), 10000\)/.test(src.capproval)
    && /\{entry\.error\.message\}/.test(src.capproval)
    && /approval-link`\, \{ instance_id: instanceId\.trim\(\) \}/.test(src.capproval)
    && !/request_key/.test(src.capproval),
  '10 秒轮询无退避；entry/autoLink 原文上屏；手动关联无请求键；组件无调用点',
)

check('F05-20', 'uploadFile 无请求键/checksum 去重', 'CONFIRMED',
  /export async function uploadFile\(/.test(src.fileApi)
    && !/request_key/.test(between(src.fileApi, 'export async function uploadFile', 'export function deleteFile'))
    && !/UniqueConstraint/.test(between(src.fileModel, 'class BusinessFile', 'class ')),
  '所有上传入口共用无键 uploadFile；后端直接 FileRecord + BusinessFile',
)

const pending1 = /if doc\.quote_version_id is None:\s*\n\s*return/.test(src.contractSvc)
const pending2 = !/<CustomerApproval|<ContractOaForm/.test(src.docs)
check('F05-P1', '无版本合同是否允许签署（业务口径）', 'PENDING',
  pending1 && /不选也能先备条款/.test(src.docs),
  '后端 quote_id 存在、quote_version_id 为空时按“整份报价”放行；UI 说登记前必须补版本，需业务定口径',
)
check('F05-P2', 'CustomerApproval / ContractOaForm 是否废弃', 'REFUTED',
  !/<CustomerApproval/.test(src.docs)
    && !/<ContractOaForm/.test(src.oaform)
    && /<ContractOaForm/.test(src.docs),
  'ContractOaForm 已在 DocumentsPage:1175 接线；只有 CustomerApproval 仍无调用点，原“两个都未接线”不成立',
)

for (const r of results) {
  console.log(`${r.ok ? r.verdict.padEnd(10) : 'MISSING   '} ${r.id} ${r.name}`)
  console.log(`           ${r.evidence}`)
}
const counts = ['CONFIRMED', 'PENDING', 'REFUTED'].map((v) => results.filter((r) => r.ok && r.verdict === v).length)
console.log(`\n${counts[0]} confirmed, ${counts[1]} pending, ${counts[2]} refuted, ${results.filter((r) => !r.ok).length} missing`)
