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
