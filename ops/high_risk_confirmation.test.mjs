import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import test from 'node:test'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript')

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
  ['wecom/WeComPage.tsx', 'syncMutation'],
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
        let attribute = node.parent
        while (attribute && !ts.isJsxAttribute(attribute)) attribute = attribute.parent
        assert.equal(attribute?.name.getText(source), 'onConfirm', `${mutation} must not run on the initial click`)
        assert.equal(attribute.parent.parent.tagName.getText(source), 'Popconfirm')
      }
      ts.forEachChild(node, visit)
    }
    visit(source)
    assert.ok(sites > 0, 'request site must exist; update this regression if the workflow moves')
  })
}
