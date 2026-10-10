import { useState } from 'react'
import { Modal, Typography } from '@douyinfe/semi-ui'

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

/** 展示后端那段"点了会怎样"的确认弹窗。 */
export function ConfirmRequiredModal(props: {
  visible: boolean
  message: string
  onConfirm: () => void
  onCancel: () => void
  confirmText?: string
}) {
  const { visible, message, onConfirm, onCancel, confirmText = '确认执行' } = props
  return (
    <Modal
      title="这个操作需要确认"
      visible={visible}
      onOk={onConfirm}
      onCancel={onCancel}
      okText={confirmText}
      cancelText="取消"
      width={560}
      style={{ maxWidth: 'calc(100vw - 48px)' }}
    >
      <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', lineHeight: 1.9, fontSize: 13 }}>
        {message}
      </Typography.Paragraph>
    </Modal>
  )
}
