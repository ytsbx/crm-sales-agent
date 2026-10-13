import assert from 'node:assert'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { runInNewContext } from 'node:vm'
import test from 'node:test'

/**
 * 报价「这一步还差什么」清单的判据单测。
 *
 * 主人 2026-10-13：把阻塞原因收成一个待办区，每项直接链接到处理位置。
 * 这份清单的价值全在**判得准**上：
 *   - 漏一项 → 用户又回到"自己找卡在哪"；
 *   - 编一项 → 用户去点了发现能点（或点了没用），就不信这个区了。
 * 所以这里把每个阶段的入口/出口都钉住，包括终态与"前面还没处理完"的组合。
 *
 * 手法照 `ops/blocked_hint.test.mjs`：直接编译前端 TS 在 vm 里跑，
 * 不用浏览器、不用测试框架。跑法：
 *   cd frontend && node --test ../ops/quote_todo.test.mjs
 */

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript')
const source = readFileSync(new URL('../frontend/src/shared/quote-todo.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText

function load() {
  const exports = {}
  runInNewContext(compiled, { exports, module: { exports } })
  return exports
}

/** 一份"什么都还没做"的输入：草稿、无警告、无运费问题。 */
const base = {
  masterWarnings: [],
  freightUnconfirmedReason: null,
  approvalStatus: 'not_submitted',
  sentAt: null,
  acceptedAt: null,
  declinedAt: null,
  converted: false,
}

const keys = (todos) => todos.map((t) => t.key)

test('干净草稿：只提示提交审批', () => {
  const { quoteTodos, currentStage } = load()
  const todos = quoteTodos(base)
  assert.deepEqual(keys(todos), ['submit_approval'])
  assert.equal(currentStage(todos), 'draft')
})

test('主数据未确认排在最前，且给出可跳转的锚点', () => {
  const { quoteTodos, currentStage } = load()
  const todos = quoteTodos({ ...base, masterWarnings: ['SKU-A：（没有可引用的已确认主数据版本）'] })
  assert.deepEqual(keys(todos), ['master_unconfirmed', 'submit_approval'])
  assert.equal(currentStage(todos), 'master', '主数据是准备阶段，必须先处理')
  assert.ok(todos[0].anchor, '每一项处理不了的事都要有去处，否则等于把问题换个地方放着')
  assert.ok(todos[0].reason.includes('1 条'), '要把"几条"说清，不能只说"有明细没确认"')
})

test('运费未确认单列一项，且理由直接用后端给的，不自己编', () => {
  const { quoteTodos, currentStage } = load()
  const reason = '尚未填写运费：请在「附加费用」里以「物流」类型填写已确认的实际运费金额。'
  const todos = quoteTodos({ ...base, freightUnconfirmedReason: reason })
  assert.deepEqual(keys(todos), ['freight_unconfirmed', 'submit_approval'])
  assert.equal(currentStage(todos), 'cost')
  assert.equal(todos[0].reason, reason, '前端不许自己造一套运费口径')
})

test('主数据与运费同时没处理：两项都在，且主数据在前（先有明细再谈费用）', () => {
  const { quoteTodos } = load()
  const todos = quoteTodos({
    ...base,
    masterWarnings: ['SKU-A：未确认'],
    freightUnconfirmedReason: '尚未确认运费',
  })
  assert.deepEqual(keys(todos), ['master_unconfirmed', 'freight_unconfirmed', 'submit_approval'])
})

test('审批中：提示等审批 + 可撤回，且不再提示提交', () => {
  const { quoteTodos, currentStage } = load()
  const todos = quoteTodos({ ...base, approvalStatus: 'pending' })
  assert.deepEqual(keys(todos), ['approval_pending'])
  assert.equal(currentStage(todos), 'approving')
  assert.equal(todos[0].action, '撤回审批')
})

test('审批被拒：引导新建版本（这一版已经不可改）', () => {
  const { quoteTodos, currentStage } = load()
  const todos = quoteTodos({ ...base, approvalStatus: 'rejected' })
  assert.deepEqual(keys(todos), ['approval_rejected'])
  assert.equal(currentStage(todos), 'rejected')
  assert.equal(todos[0].action, '新建版本')
})

test('审批已通过：下一步是发客户，并提醒发送后锁定', () => {
  const { quoteTodos, currentStage } = load()
  const todos = quoteTodos({ ...base, approvalStatus: 'approved' })
  assert.deepEqual(keys(todos), ['send_to_customer'])
  assert.equal(currentStage(todos), 'approved')
  assert.equal(todos[0].action, '标记已发送')
  assert.ok(todos[0].reason.includes('锁定'), '发送后不可改，这件事要在点之前说')
})

test('已发送未答复：等客户，不再提示发送', () => {
  const { quoteTodos, currentStage } = load()
  const todos = quoteTodos({
    ...base,
    approvalStatus: 'approved',
    sentAt: '2026-10-13T02:00:00+00:00',
  })
  assert.deepEqual(keys(todos), ['await_customer'])
  assert.equal(currentStage(todos), 'sent')
})

test('客户已接受：下一步转订单', () => {
  const { quoteTodos, currentStage } = load()
  const todos = quoteTodos({
    ...base,
    approvalStatus: 'approved',
    sentAt: '2026-10-13T02:00:00+00:00',
    acceptedAt: '2026-10-13T03:00:00+00:00',
  })
  assert.deepEqual(keys(todos), ['convert_to_order'])
  assert.equal(currentStage(todos), 'accepted')
  assert.equal(todos[0].action, '转销售订单')
})

test('客户已拒绝：终态，不再提示任何推进动作', () => {
  const { quoteTodos, currentStage, stageProgress } = load()
  const todos = quoteTodos({
    ...base,
    sentAt: '2026-10-13T02:00:00+00:00',
    declinedAt: '2026-10-13T03:00:00+00:00',
  })
  assert.deepEqual(keys(todos), ['declined'])
  assert.equal(currentStage(todos), 'declined')
  assert.equal(stageProgress('declined'), null, '终态不该出现在进度条里')
})

test('已转订单：终态，引导去订单页', () => {
  const { quoteTodos, currentStage, stageProgress } = load()
  const todos = quoteTodos({
    ...base,
    approvalStatus: 'approved',
    sentAt: '2026-10-13T02:00:00+00:00',
    acceptedAt: '2026-10-13T03:00:00+00:00',
    converted: true,
  })
  assert.deepEqual(keys(todos), ['converted'])
  assert.equal(currentStage(todos), 'converted')
  assert.equal(stageProgress('converted'), null)
})

test('终态优先于未处理的提醒：已拒绝的单不该还在喊"去确认主数据"', () => {
  const { quoteTodos } = load()
  const todos = quoteTodos({
    ...base,
    declinedAt: '2026-10-13T03:00:00+00:00',
    masterWarnings: ['SKU-A：未确认'],
    freightUnconfirmedReason: '尚未确认运费',
  })
  // 提醒仍然列出（历史事实，页面别的地方也在显示），但**第一项必须是终态**
  assert.equal(todos[todos.length - 1].key, 'declined')
  assert.equal(todos[0].key, 'master_unconfirmed')
})

test('每一项都必须写清来源，且 key 不重复（渲染要按 key 去重）', () => {
  const { quoteTodos } = load()
  const cases = [
    base,
    { ...base, masterWarnings: ['x'] },
    { ...base, freightUnconfirmedReason: 'y' },
    { ...base, approvalStatus: 'pending' },
    { ...base, approvalStatus: 'rejected' },
    { ...base, approvalStatus: 'approved' },
    { ...base, approvalStatus: 'approved', sentAt: 's' },
    { ...base, approvalStatus: 'approved', sentAt: 's', acceptedAt: 'a' },
    { ...base, declinedAt: 'd' },
    { ...base, converted: true },
  ]
  for (const input of cases) {
    const todos = quoteTodos(input)
    assert.ok(todos.length > 0, `每个状态都该有话说：${JSON.stringify(input)}`)
    const seen = new Set()
    for (const todo of todos) {
      assert.ok(todo.source && todo.source.trim(), `缺 source：${todo.key}`)
      assert.ok(todo.reason && todo.reason.trim(), `缺 reason：${todo.key}`)
      assert.ok(!seen.has(todo.key), `key 重复：${todo.key}`)
      seen.add(todo.key)
    }
  }
})

test('进度：拿得到"第几步/共几步"，且推进阶段都在表里', () => {
  const { stageProgress, STAGE_ORDER } = load()
  assert.deepEqual(stageProgress('master'), { step: 1, total: STAGE_ORDER.length })
  assert.deepEqual(stageProgress('accepted'), { step: 7, total: STAGE_ORDER.length })
  assert.equal(stageProgress(null), null)
})

test('按钮分层：每个阶段的主动作唯一，且与"下一步"一致', () => {
  const { quoteActions } = load()
  // 推进阶段的主动作：由阶段推出，页面只消费，不自己判
  const expected = {
    draft: '提交审批',
    approving: '撤回审批',
    rejected: '新建版本',
    approved: '标记已发送',
    sent: '登记再次发送',
    accepted: '转销售订单',
  }
  for (const [stage, label] of Object.entries(expected)) {
    const actions = quoteActions(stage)
    assert.ok(actions.primary, `${stage} 该有主动作`)
    assert.equal(actions.primary.label, label, `${stage} 的主动作文案`)
  }
})

test('按钮分层：准备阶段没有页面级主动作（该去处理的是明细里的锚点）', () => {
  const { quoteActions } = load()
  for (const stage of ['master', 'cost']) {
    assert.equal(
      quoteActions(stage).primary,
      null,
      `${stage} 的主动作在页面内容里（待办项带 anchor），顶部不该硬塞一个按钮`,
    )
  }
})

test('按钮分层：终态不再突出任何推动作', () => {
  const { quoteActions } = load()
  assert.equal(quoteActions('converted').primary, null, '已转订单不该还劝用户点下一步')
})

test('按钮分层：已发送阶段的次要动作保留"客户接受"（同一阶段两件事都要看得见）', () => {
  const { quoteActions } = load()
  const secondary = quoteActions('sent').secondary.map((a) => a.label)
  assert.ok(secondary.includes('客户接受'), `实际：${secondary}`)
})

test('按钮分层：收起的是低频入口，且不含任何推进动作', () => {
  const { SECONDARY_ACTIONS } = load()
  const pushing = ['提交审批', '撤回审批', '标记已发送', '转销售订单', '客户接受']
  for (const label of pushing) {
    assert.ok(
      !SECONDARY_ACTIONS.includes(label),
      `「${label}」是推进动作，不能收进"更多操作"，否则用户又找不到下一步`,
    )
  }
  assert.ok(SECONDARY_ACTIONS.includes('下载 PDF'), '下载属于低频，应该收起来')
  assert.ok(SECONDARY_ACTIONS.includes('新建版本'), '新建版本属于低频，应该收起来')
})

test('按钮分层：handler 不许为空串（页面靠它接线，空串会静默点不动）', () => {
  const { quoteActions } = load()
  const stages = ['draft', 'approving', 'rejected', 'approved', 'sent', 'accepted']
  for (const stage of stages) {
    const { primary, secondary } = quoteActions(stage)
    if (primary) assert.ok(primary.handler && primary.handler.trim(), `${stage} primary.handler 为空`)
    for (const item of secondary) {
      assert.ok(item.handler && item.handler.trim(), `${stage} secondary.handler 为空：${item.label}`)
    }
  }
})
