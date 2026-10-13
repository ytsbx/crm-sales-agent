// 实际登录Store与QueryClient；不连接业务服务器。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const require = createRequire(import.meta.url)
// 查询库按Node环境初始化，避免模拟window使孤立旧查询留下浏览器GC计时器。
const { QueryObserver } = require('@tanstack/react-query')
const values = new Map()
globalThis.localStorage = {
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, value),
  removeItem: (key) => values.delete(key),
}
globalThis.window = { localStorage: globalThis.localStorage }
require.extensions['.ts'] = (module, filename) => {
  const code = ts.transpileModule(readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText
  module._compile(code, filename)
}
const auth = require(fileURLToPath(new URL('../src/shared/store/auth.ts', import.meta.url))).useAuthStore
const { queryClient } = require(fileURLToPath(new URL('../src/shared/query-client.ts', import.meta.url)))
const key = ['quote-version', 10]

try {
  auth.getState().setAuth('A', { id: 1 })
  queryClient.setQueryData(key, { privateOwner: 'A', cost: 70 })
  auth.getState().clear()
  assert.equal(queryClient.getQueryData(key), undefined)
  auth.getState().setAuth('B', { id: 2 })
  const observer = new QueryObserver(queryClient, {
    queryKey: key, retry: false,
    queryFn: async () => { throw new Error('403: B cannot access A quote') },
  })
  assert.equal(observer.getCurrentResult().data, undefined)
  await observer.refetch()
  assert.equal(observer.getCurrentResult().status, 'error')
  assert.equal(observer.getCurrentResult().data, undefined)
  observer.destroy()

  auth.getState().setAuth('A', { id: 1 })
  let resolveOld
  const pending = queryClient.fetchQuery({
    queryKey: key,
    queryFn: () => new Promise((resolve) => { resolveOld = resolve }),
  }).catch((error) => error)
  await Promise.resolve()
  auth.getState().clear()
  auth.getState().setAuth('B', { id: 2 })
  resolveOld({ privateOwner: 'A', cost: 70 })
  await pending
  assert.equal(queryClient.getQueryData(key), undefined)

  queryClient.setQueryData(key, { privateOwner: 'B' })
  auth.getState().setAuth('C', { id: 3 })
  assert.equal(queryClient.getQueryData(key), undefined)
  console.log('PASS: logout, account switch, denied refetch, and late old response keep caches isolated')
} finally {
  queryClient.clear()
}
