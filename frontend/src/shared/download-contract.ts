/**
 * 下载合同的生成稿，并且**把失败原因说出来**（第十一批 11.3）。
 *
 * 为什么要有这一层：`api.download` 拿到 4xx 时会抛错，而两个调用点原先都是
 * `onClick={() => void downloadContractDocument(record)}` —— `void` 掉一个可能
 * 失败的 Promise，用户点完**什么反馈都没有**，只会以为网络卡了、反复点。
 * 2026-10-08 起下载失败不再"静默退回重新渲染"，而是明确报错，所以更必须让这句
 * 话露出来：原件丢失（"存档原件不可用…"）、没权限（"…不在你的可见范围内"）、
 * 网络失败，三者的文案不同，用户据此就知道该找谁。
 *
 * 抽成一个函数而不是在两个地方各写一遍 try/catch：两处（合同台账、业务详情页的
 * 合同面板）漏一处，那个入口就又会变成"点了没反应"。
 */

import { Toast } from '@douyinfe/semi-ui'

import { downloadContractDocument } from './api/contract'

export async function downloadContractDoc(doc: { id: number; doc_no: string }): Promise<void> {
  try {
    await downloadContractDocument(doc)
  } catch (error) {
    // 文案兜底由 `toast-guard` 统一负责，这里只管把原因递出去
    Toast.error(error instanceof Error ? error.message : '下载失败')
  }
}
