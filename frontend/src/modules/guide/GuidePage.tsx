import { Link } from 'react-router-dom'
import { Tag } from '@douyinfe/semi-ui'

import PageHeader from '../../shared/components/PageHeader'
import SectionCard from '../../shared/components/SectionCard'

/**
 * 流程指引页。
 *
 * 为什么要有它：新同事拿到账号后，看到的是十几个平级菜单，不知道
 * “线索 → 客户 → 商机 → 报价 → 订单 → 回款”的先后，也不知道某一步
 * 被拦住时该找谁。各业务页里的灰按钮提示只解释“当前这一步为什么不能点”，
 * 解释不了“整条路怎么走”。这一页补的就是全局视角。
 *
 * 口径：
 * - 只写**当前系统真实存在的入口和动作**，不写“以后会有”；
 * - 卡点一律写“看哪里 / 找谁”，不复制后端报错原文；
 * - 涉及权限的动作写“需要相应权限”，不猜测具体角色名。
 */

type FlowStep = {
  no: number
  title: string
  entry: string
  path: string
  who: string
  what: string
  gate: string
  exit: string
}

const STEPS: FlowStep[] = [
  {
    no: 1,
    title: '线索',
    entry: '线索中心',
    path: '/leads',
    who: '业务员 / 主管',
    what: '记录展会、转介绍、企微等来源的原始商机；补齐联系人后转成客户与商机。',
    gate: '转化时选「新建客户」会同时生成联系人和商机，记得核对客户归属。',
    exit: '可废弃、释放公海、删除后恢复；转化本身不可撤销。',
  },
   {
    no: 2,
    title: '客户',
    entry: '客户中心',
    path: '/customers',
    who: '业务员 / 主管',
    what: '维护客户资料、联系人、归属和公海；客户是报价、合同、订单的挂靠主体。',
    gate: '客户必须有负责人，否则合同、报价会以「请先补齐归属」被拦。',
    exit: '可转交、释放公海、领取、软删除并恢复。',
  },
  {
    no: 3,
    title: '商机',
    entry: '商机中心',
    path: '/opportunities',
    who: '业务员 / 主管',
    what: '把客户需求拆成商机，按阶段推进：需求确认 → 产品推荐 → 核价 → 已报价 → 样品 → 谈判 → 待下单。',
    gate: '确认成交前，商机下必须有已发送或已被客户接受的报价版本。',
    exit: '可失单、重开；有下游单据时删除会被拦并指路。',
  },
  {
    no: 4,
    title: '核价',
    entry: '价格中心 › 核价试算',
    path: '/prices',
    who: '价格管理员 / 有权限的销售',
    what: '按产品成本、客户等级、运费、目标毛利试算建议价；正式报价的明细成本也来自这里。',
    gate: '成本四项不完整不能免审；主数据（名称/规格/单位）未确认会影响正式发送。',
    exit: '成本可到期、价格规则可删除；已用于报价的快照不会被回写。',
  },
  {
    no: 5,
    title: '报价',
    entry: '报价中心',
    path: '/quotes',
    who: '业务员 / 主管',
    what: '建报价单 → 加明细 → 填运费 → 提交审批 → 发送客户 → 记录客户接受/拒绝 → 转订单。',
    gate: '主数据已确认、运费已确认（0 元也要显式填）、审批通过、未过有效期，才能正式发送。',
    exit: '待审批可撤回；未转订单可删除；客户可拒绝；过期可标记；改内容要新建版本。',
  },
  {
    no: 6,
    title: '打样 / 合同',
    entry: '样品管理 / 文档管理',
    path: '/samples',
    who: '业务员 / 主管 / 合同经办',
    what: '客户要样就走打样（同序 OA 审批后制作、寄样、签收、客户确认）；要签合同就走文档管理（上传原文件、同序 OA 审批、回签归档）。',
    gate: '打样审批未通过不能制作/寄样；合同审批未通过不能登记回签。审批通过 ≠ 已签署。',
    exit: '打样草稿/驳回可删，OA 审批可撤回；合同可撤回 OA、作废（已签需主管）、删除草稿。',
  },
  {
    no: 7,
    title: '订单',
    entry: '订单中心 / 订单草稿',
    path: '/orders',
    who: '业务员 / 主管 / 财务',
    what: '报价被客户接受后生成订单草稿，核对明细与付款条件，确认后转正式订单；再做应收计划、排发货批次、登记实发。',
    gate: '草稿必须与客户接受的报价版本逐项一致；正式下单前必须记录客户接受。',
    exit: '订单可取消（已签合同会拦并指路；已确认回款要先由财务作废；已完成订单取消要填纠错理由）；未发货批次可取消；已发货后可登记销售退货（主管复核 → 财务按人工填的最终货款抵客户欠款/退款）；交期变更可作废。',
  },
  {
    no: 8,
    title: '回款',
    entry: '回款中心 / 订单详情',
    path: '/receivables',
    who: '财务',
    what: '按应收期登记客户实际打款，上传凭证，财务确认后计入已收；没收回的期次继续催收。',
    gate: '应收计划由财务生成；业务员只有查看权限时不能登记/确认回款。',
    exit: '待确认回款可驳回、可修改；已确认回款可由财务「作废回款」（填原因，把对应应收期和已收金额恢复，记录和凭证保留）。',
  },
]

const BRANCHES = [
  {
    title: '定制询价',
    path: '/inquiries',
    desc: '客户要的产品还没有标准 SKU：先记录需求，再生成内部报价草稿；正式对外报价仍要走核价/复核。可归档、可修订、可删除。',
  },
  {
    title: '案例库',
    path: '/cases',
    desc: '把成交过程沉淀成可复用案例；提交审核后发布，发布后可撤下，修订会生成新版本。',
  },
  {
    title: '新品洞察',
    path: '/insights',
    desc: '记录市场/客户的新品信号，评审通过后可转成定制询价线索；一条洞察只能转出一条需求。',
  },
]

const BLOCKERS = [
  ['报价能建、但正式发送被拦', '多半是 SKU 主数据未确认，或运费没显式确认。', '价格中心确认主数据；报价明细「附加费用」里填物流费（0 元也要填）。若这一版已审批，只能新建版本后再接主数据。'],
  ['提交审批提示成本不完整', '成本四项没有填全，系统不能按毛利免审。', '找价格管理员在价格中心补齐生效成本，再重新生成明细。'],
  ['合同建单提示「请先补齐归属」', '客户没有负责人，系统无法判断数据范围。', '到客户中心分配负责人（主管有分配权限），再回来建合同。'],
  ['业务员点「生成应收」没权限', '应收计划属于财务动作，需要 payment:manage。', '把订单交给财务生成；业务员只查看应收与到账情况。'],
  ['打样不能登记制作/寄样', '同序 OA 审批还没通过，或 CRM 还没同步到最新结果。', '打开打样详情看审批状态；在同序 OA 办理后点「同步」，再执行正式动作。'],
  ['合同不能登记回签', '同序 OA 审批未通过，或还没上传客户签回件。', '先看合同详情的「下一步」提示；审批通过后上传客户回签件，通过不等于已签。'],
  ['订单不能取消', '已签合同，或还有已确认回款没作废，会挡住取消。', '已签合同先到文档管理作废；已确认回款请财务在「回款记录」点「作废回款」并填原因，作废后即可取消。已完成订单取消还要填纠错理由。'],
  ['已发货批次不能取消 / 客户退货', '发货是既成事实，批次不能取消，订单也不能为了退货而删；退货要单独登记。', '打开订单详情 →「退货记录」→ 登记退货：只填退几件、哪个批次、什么原因，不填金额；主管复核通过后（主管自己登记的也可以自己复核，列表会标「本人复核」；业务员不能复核），财务按和客户谈定的最终货款人工填写，系统先把客户还没付的钱抵掉、剩余转退款，实际退款后再登记流水。'],
  ['订单草稿不用了', '草稿当前没有作废/删除入口。', '先不要点「确认正式下单」；告诉主管或管理员，等系统补作废动作后再清理。'],
  ['报价发了、我方不做了', '当前只有客户拒绝/过期，没有「我方关闭」。', '先按真实原因记录跟进；不要写成客户拒绝以免漏斗失真，等系统补「报价关闭」后再统一处理。'],
]

const EXITS = [
  ['报价', '撤回审批（待审批时）；删除（未转正式订单）；客户拒绝；过期；新建版本'],
  ['打样', '删除（仅草稿或已驳回）；同序 OA 撤回后落为驳回再删；开修订版'],
  ['合同', '撤回同序 OA；作废（已签需主管）；删除草稿；从回收站恢复'],
  ['订单', '取消（未签合同；有已确认回款要先作废；已完成订单要填理由）；未发货批次可取消；交期变更可作废'],
  ['退货', '登记后待复核/待财务时可取消；主管可驳回并释放可退数量；财务处理后未退款可作废（原来抵掉的客户欠款恢复）；已登记实际退款不能作废'],
  ['回款', '待确认可驳回/修改；已确认可由财务作废（填原因，把对应欠款和已收金额恢复）'],
  ['客户', '转交、释放公海、领取、软删/恢复'],
  ['商机', '失单、重开、删除（先处理下游单据）'],
  ['任务', '取消；已取消不能再完成'],
  ['询价', '归档、删除、修订、改回待评估'],
  ['案例', '撤下、驳回、修订'],
]

const ROLES = [
  {
    role: '业务员',
    color: 'var(--crm-primary)',
    items: [
      '线索进来先转客户，核对客户归属是不是自己',
      '商机阶段按实际进展推进，成交前必须有客户接受的报价',
      '报价发出前检查三件事：主数据、运费、有效期',
      '订单草稿确认前，逐项核对明细和付款条件',
      '收款由财务登记确认，业务员看进度、催客户',
    ],
  },
  {
    role: '销售主管',
    color: 'var(--crm-warning, #d97706)',
    items: [
      '审批报价、处理客户分配与转交',
      '已签合同作废需要主管确认',
      '客户取消/我方放弃时，先判断卡在哪一步，再按「撤回与终止」处理',
      '有下游单据时不要绕过流程硬删，先处理订单/合同',
    ],
  },
  {
    role: '财务',
    color: 'var(--crm-success, #15803d)',
    items: [
      '订单确认后生成应收计划，核对定金/尾款比例',
      '登记客户实际打款，确认前核对金额、日期、凭证',
      '待确认回款可驳回；已确认回款由财务作废并填原因（把对应欠款和已收金额恢复，记录保留）',
      '订单取消前，先确认有没有已确认回款；有就先作废再取消',
    ],
  },
]

function StepCard({ step }: { step: FlowStep }) {
  return (
    <div
      style={{
        border: '1px solid var(--crm-border)',
        borderRadius: 'var(--crm-radius, 8px)',
        padding: 16,
        background: 'var(--crm-bg-elevated, #fff)',
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        minWidth: 0,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span
          aria-hidden
          style={{
            width: 24,
            height: 24,
            borderRadius: '50%',
            background: 'var(--crm-primary-soft, #e8f0ff)',
            color: 'var(--crm-primary)',
            fontWeight: 600,
            fontSize: 13,
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            flex: 'none',
          }}
        >
          {step.no}
        </span>
        <Link to={step.path} style={{ fontWeight: 600, fontSize: 15 }}>
          {step.title}
        </Link>
        <Tag size="small" style={{ marginLeft: 'auto' }}>{step.entry}</Tag>
      </div>
      <div style={{ fontSize: 13, color: 'var(--crm-text-2)' }}>{step.what}</div>
      <div style={{ fontSize: 12, color: 'var(--crm-text-2)' }}>
        <strong>谁来做：</strong>{step.who}
      </div>
      <div style={{ fontSize: 12, color: 'var(--crm-text-2)' }}>
        <strong>前置/卡点：</strong>{step.gate}
      </div>
      <div style={{ fontSize: 12, color: 'var(--crm-text-2)' }}>
        <strong>可撤回：</strong>{step.exit}
      </div>
    </div>
  )
}

export default function GuidePage() {
  return (
    <div className="page-container">
      <PageHeader
        title="流程指引"
        subtitle="从线索到回款：先走哪一步、卡住看哪里、反悔了怎么退。本页只做指引，具体规则以页面提示和后端校验为准。"
      />

      <SectionCard title="主线：一条单子怎么走" accent="var(--crm-primary)">
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))',
            gap: 12,
          }}
        >
          {STEPS.map((step) => <StepCard key={step.no} step={step} />)}
        </div>
      </SectionCard>

      <SectionCard title="支线：不是每单都走" style={{ marginTop: 16 }} accent="var(--crm-info, #2563eb)">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12 }}>
          {BRANCHES.map((branch) => (
            <div
              key={branch.title}
              style={{
                border: '1px solid var(--crm-border)',
                borderRadius: 'var(--crm-radius, 8px)',
                padding: 16,
              }}
            >
              <Link to={branch.path} style={{ fontWeight: 600 }}>{branch.title}</Link>
              <p className="workflow-guide" style={{ marginTop: 6 }}>{branch.desc}</p>
            </div>
          ))}
        </div>
      </SectionCard>

      <SectionCard title="卡住了怎么办：先看这张表" style={{ marginTop: 16 }} accent="var(--crm-warning, #d97706)">
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: 'left', borderBottom: '1px solid var(--crm-border)' }}>
                <th style={{ padding: '8px 10px', width: '26%' }}>现象</th>
                <th style={{ padding: '8px 10px', width: '30%' }}>多半是什么原因</th>
                <th style={{ padding: '8px 10px' }}>怎么办</th>
              </tr>
            </thead>
            <tbody>
              {BLOCKERS.map(([phenomenon, reason, action]) => (
                <tr key={phenomenon} style={{ borderBottom: '1px solid var(--crm-border-light, #f0f0f0)' }}>
                  <td style={{ padding: '8px 10px', verticalAlign: 'top' }}>{phenomenon}</td>
                  <td style={{ padding: '8px 10px', verticalAlign: 'top', color: 'var(--crm-text-2)' }}>{reason}</td>
                  <td style={{ padding: '8px 10px', verticalAlign: 'top' }}>{action}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </SectionCard>

      <SectionCard title="撤回与终止：现在能做什么" style={{ marginTop: 16 }} accent="var(--crm-success, #15803d)">
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: 'left', borderBottom: '1px solid var(--crm-border)' }}>
                <th style={{ padding: '8px 10px', width: 120 }}>环节</th>
                <th style={{ padding: '8px 10px' }}>现有撤回/终止动作</th>
              </tr>
            </thead>
            <tbody>
              {EXITS.map(([module, actions]) => (
                <tr key={module} style={{ borderBottom: '1px solid var(--crm-border-light, #f0f0f0)' }}>
                  <td style={{ padding: '8px 10px', fontWeight: 500 }}>{module}</td>
                  <td style={{ padding: '8px 10px', color: 'var(--crm-text-2)' }}>{actions}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="workflow-guide" style={{ marginTop: 12 }}>
          暂时不能的三处：打样已批准不能终止、订单草稿不能作废、已发送报价不能「我方关闭」。
          这些是业务口径待确认项，已在走查报告里列出；确认前不要为了“退掉”而删数据，先找主管/财务线下处理。
        </p>
      </SectionCard>

      <SectionCard title="按角色快速上手" style={{ marginTop: 16 }} accent="var(--crm-info, #2563eb)">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12 }}>
          {ROLES.map((role) => (
            <div
              key={role.role}
              style={{
                border: '1px solid var(--crm-border)',
                borderTop: `3px solid ${role.color}`,
                borderRadius: 'var(--crm-radius, 8px)',
                padding: 16,
              }}
            >
              <div style={{ fontWeight: 600, marginBottom: 8 }}>{role.role}</div>
              <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--crm-text-2)', fontSize: 13, lineHeight: 1.8 }}>
                {role.items.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </div>
          ))}
        </div>
      </SectionCard>

      <p className="workflow-guide" style={{ marginTop: 16 }}>
        需要改流程或加撤回动作时，先看 `docs/全流程走查与撤回点-20261125.md` 里的证据与待决策项；
        本页随功能上线同步更新，不提前承诺还没有的入口。
      </p>
    </div>
  )
}
