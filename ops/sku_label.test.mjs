import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'

const require=createRequire(new URL('../frontend/package.json',import.meta.url))
const ts=require('typescript')
const exports={}
runInNewContext(ts.transpileModule(readFileSync(new URL('../frontend/src/shared/sku-label.ts',import.meta.url),'utf8'),{
  compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022},
}).outputText,{exports})
const {skuLabel}=exports
test('名称和规格优先，编码辅助且可继续用于搜索',()=>{
  assert.equal(skuLabel({product_name:'周转箱',name:'蓝色',specification:'600×400',sku_code:'TC-01'}),'周转箱 · 蓝色 · 600×400（编码：TC-01）')
})
test('没有规格名称时显示所属产品；同名不重复',()=>{
  assert.equal(skuLabel({product_name:'周转箱',name:'周转箱',specification:'600×400',sku_code:'AUTO'}),'周转箱 · 600×400（编码：AUTO）')
})
test('旧记录缺名字时仍保留编码，不制造产品资料',()=>{
  assert.equal(skuLabel({sku_code:'OLD'}),'未命名产品（编码：OLD）')
})
