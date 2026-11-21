import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import ts from 'typescript'
const require = createRequire(import.meta.url)
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,filename)
const { approvalProgress } = require('../src/modules/knowledge/approval-progress.ts')
assert.deepEqual(approvalProgress(null),[])
assert.deepEqual(approvalProgress({payload:{tasks:'malformed',operationRecords:null}}),[])
const input={payload:{tasks:[{userId:'u',taskGroupName:'报价',status:'RUNNING',result:'NONE'}],operationRecords:[{userId:'u',showName:'同事甲',type:'EXECUTE_TASK_NORMAL',result:'agree',remark:'可以',date:'2026-10-15T01:00Z'}]}}
const result=approvalProgress(input)
assert.equal(result[0].person,'同事甲')
assert.equal(result[0].status,'处理中')
assert.equal(result[1].status,'同意')
assert.equal(result[1].remark,'可以')
assert.equal(input.payload.tasks[0].status,'RUNNING')
const unknown=approvalProgress({payload:{tasks:[{userId:'secret-id',status:'FUTURE'}]}})[0]
assert.equal(unknown.status,'状态待核对')
assert.ok(!unknown.person.includes('secret-id'))
console.log('approval progress snapshot checks passed')

const { oaFormFields } = require('../src/shared/components/oa-form-fields.ts')
assert.deepEqual(oaFormFields(null), [])
assert.deepEqual(oaFormFields({payload:{formComponentValues:[
  {name:'生产报价',value:'12.50'},
  {name:'附件',value:JSON.stringify([{fileName:'回复图纸.pdf',fileId:'internal-id'}])},
  {name:'说明',value:'<script>test</script>'},
  {name:'明细',value:JSON.stringify([{rowValue:[{label:'材质',value:'PP'}]}])},
  {name:'数量',value:0},
]}}), [
  {name:'生产报价',value:'12.50'}, {name:'附件',value:'回复图纸.pdf'},
  {name:'说明',value:'<script>test</script>'}, {name:'明细',value:'材质：PP'},
  {name:'数量',value:'0'},
])
assert.deepEqual(oaFormFields({payload:{formValueVOS:[{name:'后续回复',value:'确认'}]}}),[{name:'后续回复',value:'确认'}])
console.log('OA returned form checks passed')
