import { useState } from 'react'
import { Modal } from '@douyinfe/semi-ui'

/**
 * 需要**明确确认**才能执行的动作，统一的确认弹窗。
 *
 * 为什么要做成一个共用组件（而不是五个弹窗各写一遍）：
 * 后端在这些动作上加了"必须先确认"的判据（`app/core/confirmation.py`），
 * 不带 `confirm=true` 时返回 `42206`，**并且把"点了会怎样"写在报错文案里**。
 * 所以前端不需要自己拼文案 —— 它只负责"把后端说的话原样显示给用户，
 * 用户点确认之后带上 confirm 重发一次"。这样：
 *
 * - 文案只有后端一份，改了不用两边同步；
 * - 以后再加一个需要确认的动作，前端只要包一层 `useConfirmedAction`
 *   （或在按钮里调 `confirmThenRun`），不用重复写弹窗。
 *
 * ⚠️ **不要把它套在每个保存按钮上**（审查原话）：只保护会**切换当前依据、
 * 覆盖金额、结束流程、登记商务事实、或者写进外部系统**的动作。
 * 已经有内容核对弹窗的（例如"刷新主数据"那条预览），不要再套第二次。
 */

/** 后端"需要确认"的错误码。与 `app/core/errors.py` 的 `CONFIRM_REQUIRED` 对齐。 */
export const CONFIRM_REQUIRED_CODE = 42206

/** 判断一个接口错误是不是"需要确认"。 */
export function isConfirmRequired(error: unknown): boolean {
  const code = (error as { code?: number } | null)?.code
  return code === CONFIRM_REQUIRED_CODE
}

/** 取"需要确认"的说明文案（后端给的那段话）。 */
export function confirmMessageOf(error: unknown): string {
  return (error as { message?: string } | null)?.message ?? ''
}

export interface ConfirmGateState {
  visible: boolean
  message: string
}

/**
 * 配合 `confirmThenRun` 使用的状态钩子。
 *
 * 用法（按钮里）：
 *
 * ```tsx
 * const gate = useConfirmGate()
 * <Button onClick={() => gate.run(() => someApi({ ... }))} />
 * <ConfirmRequiredModal
 *   message={gate.message}
 *   visible={gate.visible}
 *   onCancel={gate.cancel}
 *   onConfirm={gate.proceed}
 * />
 * ```
 *
 * `run` 先用**原参数**发一次请求：
 * - 成功 → 直接算成功（这个动作本来就不需要确认，或已确认过）；
 * - 报 `42206` → 弹出后端给的说明，用户点确认后带上 `confirm` 重发。
 */
export function useConfirmGate() {
  const [state, setState] = useState<ConfirmGateState>({ visible: false, message: '' })
  const [pending, setPending] = useState<(() => Promise<unknown>) | null>(null)

  function cancel() {
    setState({ visible: false, message: '' })
    setPending(null)
  }

  async function run(action: () => Promise<unknown>): Promise<unknown> {
    try {
      return await action()
    } catch (error) {
      if (!isConfirmRequired(error)) throw error
      setPending(() => action)
      setState({ visible: true, message: confirmMessageOf(error) })
      return undefined
    }
  }

  async function proceed(): Promise<unknown> {
    const action = pending
    cancel()
    if (!action) return undefined
    return action()
  }

  return { ...state, run, proceed, cancel }
}

/**
 * 后端文案里用 `**...**` 标重点（例如「**没有撤销入口**」）。
 * 原来整段原样塞进 <p>，星号直接显示出来，像没渲染完的 Markdown；
 * 这里只认这一种标记：成对的 `**` 之间加粗 + 换成正文色，其余按原文。
 */
const RISK_PATTERN = /没有撤销|不可撤销|无法撤回|不可恢复|不能撤销|不可逆|无法撤销/

function renderEmphasis(text: string) {
  const parts = text.split(/\*\*(.+?)\*\*/g)
  return parts.map((part, index) =>
    index % 2 === 1 ? <strong key={index}>{part}</strong> : <span key={index}>{part}</span>,
  )
}

/** 展示后端那段"点了会怎样"的确认弹窗。 */
export function ConfirmRequiredModal(props: {
  visible: boolean
  message: string
  onConfirm: () => void
  onCancel: () => void
  confirmText?: string
}) {
  const { visible, message, onConfirm, onCancel, confirmText = '确认执行' } = props
  // 后端文案有时是一段、有时带换行；按行分段落，行距交给 CSS，不再靠 pre-wrap 硬撑
  const paragraphs = message.split(/\n+/).filter((line) => line.trim())
  // 长句子再拆一层：`……请确认后继续：A；B；C` 这种，把 A/B/C 摆成三条，
  // 比一整段"分号连读"好扫。没有冒号分号的保持原样，不硬拆。
  const structured = paragraphs.map((line) => {
    const idx = line.indexOf('：')
    if (idx > 0) {
      const lead = line.slice(0, idx + 1)
      const items = line
        .slice(idx + 1)
        .split(/[；;]/)
        .map((part) => part.trim())
        .filter(Boolean)
      if (items.length > 1) return { lead, items }
    }
    return { lead: '', items: [line] }
  })
  return (
    <Modal
      visible={visible}
      onOk={onConfirm}
      onCancel={onCancel}
      okText={confirmText}
      cancelText="取消"
      width={560}
      className="crm-confirm-modal"
      style={{ maxWidth: 'calc(100vw - 48px)' }}
      header={
        <div className="crm-confirm-header">
          <span className="crm-confirm-icon" aria-hidden="true">!</span>
          {/* 标题写成"要你确认的是什么事"，不是系统口吻的"这个操作需要确认" */}
          <span className="crm-confirm-title">请确认这次操作</span>
          {/* 副标题不再另起一行（原来标题下面吊一行灰字，头部显高又空）；
              改成贴在同一条水平线上靠右，像"生效说明"的标签。 */}
          <span className="crm-confirm-sub">确认后立即生效</span>
        </div>
      }
    >
      <div className="crm-confirm-body">
        {structured.map((block, index) => (
          <div key={index} className="crm-confirm-block">
            {block.lead && <p className="crm-confirm-lead">{renderEmphasis(block.lead)}</p>}
            {block.items.length === 1 && !block.lead ? (
              <p className={RISK_PATTERN.test(block.items[0]) ? 'crm-confirm-risk' : undefined}>
                {renderEmphasis(block.items[0])}
              </p>
            ) : (
              <ul className="crm-confirm-list">
                {block.items.map((item, i) => (
                  <li key={i} className={RISK_PATTERN.test(item) ? 'crm-confirm-risk' : undefined}>
                    {renderEmphasis(item)}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </Modal>
  )
}
