import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { runInNewContext } from 'node:vm'
import test from 'node:test'

const require=createRequire(new URL('../frontend/package.json',import.meta.url))
const ts=require('typescript')
const source=ts.createSourceFile('SettingsPage.tsx',readFileSync(new URL('../frontend/src/modules/settings/SettingsPage.tsx',import.meta.url),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX)
let decision
function visit(node) {
  if(ts.isVariableDeclaration(node) && node.name.getText(source)==='decide') decision=node.initializer
  ts.forEachChild(node,visit)
}
visit(source)
assert.ok(decision)
const compiled=ts.transpileModule(`exports.decide=${decision.getText(source)}`,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
function load(note=null) {
  const calls=[],gates=[],prompts=[],errors=[],exports={}
  runInNewContext(compiled,{exports,Modal:{confirm:options=>gates.push(options)},
    decideMutation:{mutate:payload=>calls.push(payload)},
    window:{prompt:message=>{prompts.push(message);return note}},
    Toast:{error:message=>errors.push(message)},fmtDay:value=>value,
  })
  return {...exports,calls,gates,prompts,errors}
}
test('ordinary approval waits for confirmation; cancel writes nothing',()=>{
  const t=load()
  t.decide('approve',{id:7,customer_name:'隔离客户',protection:[]})
  assert.equal(t.calls.length,0)
  assert.equal(t.gates.length,1)
  t.gates[0].onCancel?.()
  assert.equal(t.calls.length,0)
  t.gates[0].onOk()
  assert.equal(t.calls.length,1)
  assert.equal(t.calls[0].id,7)
  assert.equal(t.calls[0].decision,'approve')
  assert.equal(t.prompts.length,0)
})
for(const [decision,row,options] of [
  ['approve',{id:7,protection:['订单履约中']},undefined],
  ['approve',{id:7,protection:[]},{early:true}],
  ['defer',{id:7,protection:[]},undefined],
  ['reject',{id:7,protection:[]},undefined],
]) {
  test(`${decision} ${options?.early?'early':'reason'}: existing reason form handles cancel`,()=>{
    const t=load()
    t.decide(decision,row,options)
    assert.equal(t.calls.length,0)
    assert.equal(t.gates.length,0)
    assert.equal(t.prompts.length,1)
  })
}
