import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import test from 'node:test'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript')

/** 从节点往上找第一个满足谓词的祖先（不含自身）。 */
function findAncestor(node, predicate) {
  let cursor = node.parent
  while (cursor) {
    if (predicate(cursor)) return cursor
    cursor = cursor.parent
  }
  return null
}

// Guard the actual request sites, not button text: a renamed button must not bypass confirmation.
for (const [file, mutation, action] of [
  ['quote/QuoteDetailPage.tsx', 'withdrawMutation'],
  ['lead/LeadListPage.tsx', 'claimMutation'],
  ['customer/CustomerDetailPage.tsx', 'poolMutation', 'claim'],
  ['recycle/RecycleBinPage.tsx', 'restoreProductMutation'],
  ['recycle/RecycleBinPage.tsx', 'restoreSkuMutation'],
  ['settings/SettingsPage.tsx', 'dtSyncMutation'],
  ['settings/SettingsPage.tsx', 'recycleMutation'],
  ['settings/SettingsPage.tsx', 'autoTaskMutation'],
  ['settings/SettingsPage.tsx', 'userStatusMutation'],
  ['settings/NotificationSettingsPanel.tsx', 'retryMutation'],
  ['common/NotificationBell.tsx', 'redispatchMutation'],
  // wecom syncMutation 不在此列（主人 2026-11-19 拍板按产品口径）：同步是只读拉
  // 企微数据、更新本地同步记录，不动业务数据，产品有意去掉二次确认（740dc620），
  // 且成功/部分失败/失败都有 Toast 反馈、失败任务留痕可重试。bindMutation
  // （关联客户=写业务数据）仍是高危，保留。
  ['wecom/WeComPage.tsx', 'bindMutation'],
  ['cases/CasesPage.tsx', 'submitMutation'],
  ['cases/CasesPage.tsx', 'reviseMutation'],
  ['product/ProductInsightsPage.tsx', 'submitMutation'],
  ['product/ProductDetailPage.tsx', 'toggleMutation'],
  ['order/OrderDetailPage.tsx', 'repurchaseMutation'],
]) {
  test(`${file}: ${mutation} requests are reachable only through confirmation`, () => {
    const source = ts.createSourceFile(file, readFileSync(new URL(`../frontend/src/modules/${file}`, import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
    let sites = 0
    function visit(node) {
      if (ts.isCallExpression(node) && node.expression.getText(source) === `${mutation}.mutate`) {
        if (action) {
          const arg = node.arguments[0]
          const requested = arg && ts.isObjectLiteralExpression(arg)
            ? arg.properties.find(p => ts.isPropertyAssignment(p) && p.name.getText(source) === 'action')?.initializer
            : undefined
          if (!requested || !ts.isStringLiteral(requested) || requested.text !== action) {
            ts.forEachChild(node, visit)
            return
          }
        }
        sites++
        // 认两种合规的确认方式，语义等价：Popconfirm 的 onConfirm、Modal.confirm 的 onOk ——
        // 都必须**点过确认**才执行，不是首击即发。
        // 后者不是放水：下拉菜单里气泡定位会飘，把「放入公海 / 删除」收进「更多」后
        // 只能改用弹窗（客户详情 CustomerDetailPage）。任何**没有**确认包裹的调用仍然会红。
        const onOkProp = findAncestor(node, (n) => ts.isPropertyAssignment(n) && n.name.getText(source) === 'onOk')
        const confirmedByModal = Boolean(
          onOkProp &&
            findAncestor(onOkProp, (n) => ts.isCallExpression(n) && n.expression.getText(source) === 'Modal.confirm'),
        )
        if (!confirmedByModal) {
          let attribute = node.parent
          while (attribute && !ts.isJsxAttribute(attribute)) attribute = attribute.parent
          assert.equal(attribute?.name.getText(source), 'onConfirm', `${mutation} must not run on the initial click`)
          assert.equal(attribute.parent.parent.tagName.getText(source), 'Popconfirm')
        }
      }
      ts.forEachChild(node, visit)
    }
    visit(source)
    assert.ok(sites > 0, 'request site must exist; update this regression if the workflow moves')
  })
}
