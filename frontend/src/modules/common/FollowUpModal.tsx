import { useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { DatePicker, Input, Modal, Select, TextArea, Toast } from '@douyinfe/semi-ui'

import { createFollowUp } from '../../shared/api/followup'

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

  const reset = () => {
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
      title="记录跟进"
      visible={visible}
      onCancel={onClose}
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
      okText="保存"
      width={560}
    >
      <div style={{ display: 'grid', gap: 12 }}>
        <div>
          <div style={{ marginBottom: 4 }}>跟进方式</div>
          <Select
            value={followupType}
            onChange={(value) => setFollowupType(value as string)}
            optionList={TYPES}
            style={{ width: 180 }}
          />
        </div>
        <div>
          <div style={{ marginBottom: 4 }}>跟进内容 *</div>
          <TextArea
            value={content}
            onChange={setContent}
            rows={3}
            placeholder="今天沟通了什么？"
          />
        </div>
        <div>
          <div style={{ marginBottom: 4 }}>客户反馈</div>
          <TextArea value={feedback} onChange={setFeedback} rows={2} />
        </div>
        <div>
          <div style={{ marginBottom: 4 }}>后续安排</div>
          <Select
            value={exemptionReason ?? 'plan'}
            onChange={(value) => setExemptionReason(value === 'plan' ? null : String(value))}
            optionList={[
              { value: 'plan', label: '安排下一次跟进' },
              { value: 'customer_declined', label: '免填：客户明确拒绝' },
              { value: 'business_closed', label: '免填：业务已关闭' },
              { value: 'waiting_external', label: '免填：等待外部固定节点' },
            ]}
            style={{ width: '100%' }}
          />
        </div>
        {!exemptionReason && <>
          <div>
            <div style={{ marginBottom: 4 }}>下一步动作 *</div>
            <Input value={nextAction} maxLength={200} onChange={setNextAction} placeholder="例如：整理报价并回访客户" />
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>下次跟进时间 *</div>
            <DatePicker
              type="dateTime"
              value={taskDue ?? undefined}
              onChange={(date) => setTaskDue((date as Date) ?? null)}
              placeholder="选择下次跟进时间"
              style={{ width: '100%' }}
            />
            <div style={{ marginTop: 6, color: 'var(--semi-color-text-2)', fontSize: 12 }}>保存后自动生成后续待办。</div>
          </div>
        </>}
        {exemptionReason && <div style={{ color: 'var(--semi-color-text-2)', fontSize: 12 }}>保存免填原因，本次不创建后续待办。</div>}

      </div>
    </Modal>
  )
}
