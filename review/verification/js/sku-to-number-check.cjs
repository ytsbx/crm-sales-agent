// 从 ProductDetailPage.tsx 抽取真实的 toNumber 函数体，转译后执行。
const fs = require('fs')
const path = require('path')
const ts = require('/Users/zhaorongkai/Desktop/CRM-Sales-Agent-V1.1/frontend/node_modules/typescript')

const file = path.join(__dirname, '../../../frontend/src/modules/product/ProductDetailPage.tsx')
const src = fs.readFileSync(file, 'utf8')
const match = src.match(/const toNumber = \(value: string\): number \| null => \{[\s\S]*?\n\}/)
if (!match) throw new Error('未找到 toNumber')
const js = ts.transpileModule(match[0] + '\nglobalThis.__toNumber = toNumber', {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS },
}).outputText
eval(js)

for (const input of ['120.500', 'abc', '', '1.2.3']) {
  const out = globalThis.__toNumber(input)
  console.log(`[F12-01] toNumber(${JSON.stringify(input)}) = ${out}; PATCH载荷=${JSON.stringify({ length: out })}`)
}
