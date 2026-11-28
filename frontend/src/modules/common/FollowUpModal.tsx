import { useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { DatePicker, Input, Modal, Select, TextArea, Toast } from '@douyinfe/semi-ui'

import { createFollowUp } from '../../shared/api/followup'
import { otherOption } from '../../shared/components/otherOption'

// 跟进方式。值是中文本身（后端 `followup_type` 是自由文本），
// 选中「其他」后写的内容直接存回该字段。
const TYPES = ['电话', '微信', '企业微信', '拜访', '邮件', '其他'].map((value) => ({
  value,
  label: value,
}))

export interface FollowUpTarget {
  customerId?: number
  contactId?: number
  opportunityId?: number
  leadId?: number
}

interface Props {
  visible: boolean
  onClose: () => void
  target: FollowUpTarget
  onCreated?: () => void
}

export default function FollowUpModal({ visible, onClose, target, onCreated }: Props) {
  const queryClient = useQueryClient()
  const [followupType, setFollowupType] = useState('电话')
  const [content, setContent] = useState('')
  const [feedback, setFeedback] = useState('')
  const [nextAction, setNextAction] = useState('')
  const [exemptionReason, setExemptionReason] = useState<string | null>(null)
  const submission = useRef<{ signature: string; key: string } | null>(null)
  const [taskDue, setTaskDue] = useState<Date | null>(null)

  // 跟进方式的「其他」：选中后多一个输入框写具体方式（不填就保持「其他」）。
  const typeOption = otherOption({
    options: TYPES.map((item) => item.value),
    value: followupType,
    onChange: setFollowupType,
  })

  const reset = () => {
    setFollowupType('电话')
    setContent('')
    setFeedback('')
    setNextAction('')
    setExemptionReason(null)
    submission.current = null
    setTaskDue(null)
  }

  const mutation = useMutation({
    mutationFn: () => {
      const payload = {
        followup_type: followupType,
        content: content.trim(),
        customer_feedback: feedback || null,
        next_action: exemptionReason ? null : nextAction.trim(),
        exemption_reason: exemptionReason,
        customer_id: target.customerId ?? null,
        contact_id: target.contactId ?? null,
        opportunity_id: target.opportunityId ?? null,
        lead_id: target.leadId ?? null,
        task_due_at: !exemptionReason && taskDue ? taskDue.toISOString() : null,
      }
      const signature = JSON.stringify(payload)
      if (submission.current?.signature !== signature) {
        submission.current = { signature, key: Array.from(crypto.getRandomValues(new Uint8Array(16)), (value) => value.toString(16).padStart(2, '0')).join('') }
      }
      return createFollowUp({ ...payload, request_key: submission.current.key })
    },
    onSuccess: (data) => {
      Toast.success(data.task_id ? '跟进已记录，并生成了后续任务' : '跟进已记录')
      reset()
      onClose()
      void queryClient.invalidateQueries({ queryKey: ['followups'] })
      void queryClient.invalidateQueries({ queryKey: ['timeline'] })
      void queryClient.invalidateQueries({ queryKey: ['tasks'] })
      void queryClient.invalidateQueries({ queryKey: ['customer'] })
      void queryClient.invalidateQueries({ queryKey: ['customers'] })
      onCreated?.()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  return (
    <Modal
      title={
          <div className="modal-head">
            <span className="modal-head-icon" aria-hidden>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"><path d="M4 5h16v11H9l-4 4v-4H4z" /><path d="M8 10h8" /></svg>
            </span>
            <div>
              记录跟进
              <div className="modal-subtitle">先记这次沟通，再决定要不要生成下次的待办</div>
            </div>
          </div>
        }
      visible={visible}
      onCancel={() => {
        if (mutation.isPending) return
        if (!submission.current) reset()
        onClose()
      }}
      onOk={() => {
        if (!content.trim()) {
          Toast.warning('跟进内容必填')
          return
        }
        if (!exemptionReason && (!nextAction.trim() || !taskDue)) {
          Toast.warning('请填写下一动作和下次跟进时间，或选择免填原因')
          return
        }
        mutation.mutate()
      }}
      confirmLoading={mutation.isPending}
      cancelButtonProps={{ disabled: mutation.isPending }}
      okText={exemptionReason ? '只保存跟进' : '保存并建待办'}
      width={600}
    >
      <div className="form-cards">
        <section className="form-card">
          <div className="form-card-title">沟通记录</div>
          <div className="form-card-grid">
            <div className="full">
              <div className="form-field-label">跟进方式</div>
              <Select
                value={typeOption.selectValue}
                onChange={typeOption.onSelect}
                optionList={TYPES}
                style={{ width: '100%' }}
              />
              {typeOption.showInput && (
                <Input
                  style={{ marginTop: 8 }}
                  value={typeOption.inputValue}
                  onChange={typeOption.onInput}
                  placeholder="请说明是什么方式（可不填）"
                />
              )}
            </div>
            <div className="full">
              <div className="form-field-label">跟进内容<span className="req">*</span></div>
              <TextArea
                value={content}
                onChange={setContent}
                rows={3}
                placeholder="今天沟通了什么？"
              />
            </div>
            <div className="full">
              <div className="form-field-label">客户反馈</div>
              <TextArea value={feedback} onChange={setFeedback} rows={2} placeholder="客户原话、顾虑、比价情况……" />
            </div>
          </div>
        </section>

        <section className="form-card">
          <div className="form-card-title">下次跟进</div>
          <div className="form-card-grid">
            <div className="full">
              <div className="form-field-label">后续安排</div>
              <Select
                value={exemptionReason ?? 'plan'}
                onChange={(value) => setExemptionReason(value === 'plan' ? null : String(value))}
                optionList={[
                  { value: 'plan', label: '安排下一次跟进' },
                  { value: 'customer_declined', label: '只记跟进：客户明确拒绝' },
                  { value: 'business_closed', label: '只记跟进：业务已关闭' },
                  { value: 'waiting_external', label: '只记跟进：等待外部固定节点' },
                ]}
                style={{ width: '100%' }}
              />
            </div>
            {!exemptionReason && <>
              <div>
                <div className="form-field-label">下一步动作<span className="req">*</span></div>
                <Input value={nextAction} maxLength={200} onChange={setNextAction} placeholder="例如：整理报价并回访客户" />
              </div>
              <div>
                <div className="form-field-label">下次跟进时间<span className="req">*</span></div>
                <DatePicker
                  type="dateTime"
                  value={taskDue ?? undefined}
                  onChange={(date) => setTaskDue((date as Date) ?? null)}
                  placeholder="选择下次跟进时间"
                  style={{ width: '100%' }}
                />
              </div>
              <div className="full form-tip">保存后自动生成后续待办。可在「销售任务」里查看，所选时间是待办的截止时间。</div>
            </>}
            {exemptionReason && (
              <div className="full form-tip">原因随跟进记录保存，本次不创建待办。</div>
            )}
          </div>
        </section>
      </div>
    </Modal>
  )
}
