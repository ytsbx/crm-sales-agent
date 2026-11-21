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
  ['settings/SettingsPage.tsx', 'userStatusMutation'],
  ['settings/NotificationSettingsPanel.tsx', 'retryDelivery'],
  ['common/NotificationBell.tsx', 'redispatchMutation'],
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
          // 自定义弹窗里的 GuardedButton 也是"点过确认才执行"：用户先开弹窗、
          // 选好对象，再点 footer 的确认按钮；不是首击即发。允许这种确认方式，
          // 但仍然要求按钮是 GuardedButton 且位于 Modal 内。
          const tagName = attribute?.parent?.parent?.tagName?.getText(source)
          const guardedInModal = tagName === 'GuardedButton' && Boolean(
            findAncestor(attribute, (n) =>
              ts.isJsxElement(n) && n.openingElement.tagName.getText(source) === 'Modal'),
          )
          if (!guardedInModal) {
            assert.equal(attribute?.name.getText(source), 'onConfirm', `${mutation} must not run on the initial click`)
            assert.equal(attribute.parent.parent.tagName.getText(source), 'Popconfirm')
          }
        }
      }
      ts.forEachChild(node, visit)
    }
    visit(source)
    assert.ok(sites > 0, 'request site must exist; update this regression if the workflow moves')
  })
}

// 自动待办扫描的确认按钮搬到了子组件：SettingsPage 只把 `onScan` 回调传下去，
// 真正的点击入口在 AutoTaskRulesPanel。这里守住子组件：onScan 只能挂在
// Popconfirm 的 onConfirm 上，不能挂在按钮 onClick 上首击即发。
test('settings/AutoTaskRulesPanel.tsx: onScan runs only after confirmation', () => {
  const file = 'settings/AutoTaskRulesPanel.tsx'
  const source = ts.createSourceFile(file, readFileSync(new URL(`../frontend/src/modules/${file}`, import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  let confirmed = false
  let firstClick = false
  function visit(node) {
    if (ts.isJsxAttribute(node)) {
      const name = node.name.getText(source)
      const text = node.getText(source)
      if (name === 'onConfirm' && text.includes('onScan')) {
        if (node.parent.parent.tagName?.getText(source) === 'Popconfirm') confirmed = true
      }
      if (name === 'onClick' && text.includes('onScan')) firstClick = true
    }
    ts.forEachChild(node, visit)
  }
  visit(source)
  assert.ok(confirmed, 'onScan must be bound to Popconfirm onConfirm')
  assert.equal(firstClick, false, 'onScan must not run on the initial click')
})
