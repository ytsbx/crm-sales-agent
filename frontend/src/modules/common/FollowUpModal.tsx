import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Checkbox, DatePicker, Input, Modal, Select, TextArea, Toast } from '@douyinfe/semi-ui'

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
  const [withTask, setWithTask] = useState(false)
  const [taskTitle, setTaskTitle] = useState('')
  const [taskDue, setTaskDue] = useState<Date | null>(null)

  const reset = () => {
    setContent('')
    setFeedback('')
    setNextAction('')
    setWithTask(false)
    setTaskTitle('')
    setTaskDue(null)
  }

  const mutation = useMutation({
    mutationFn: () =>
      createFollowUp({
        followup_type: followupType,
        content,
        customer_feedback: feedback || null,
        next_action: nextAction || null,
        customer_id: target.customerId ?? null,
        contact_id: target.contactId ?? null,
        opportunity_id: target.opportunityId ?? null,
        lead_id: target.leadId ?? null,
        create_task: withTask,
        task_title: withTask ? taskTitle || null : null,
        task_due_at: withTask && taskDue ? taskDue.toISOString() : null,
      }),
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
        if (withTask && !taskDue) {
          Toast.warning('要创建后续任务，请选择任务时间')
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
          <div style={{ marginBottom: 4 }}>下一步动作</div>
          <Input value={nextAction} onChange={setNextAction} placeholder="例如：明天整理报价" />
        </div>
        <div>
          <Checkbox checked={withTask} onChange={(event) => setWithTask(Boolean(event.target.checked))}>
            同时创建后续任务
          </Checkbox>
          {withTask && (
            <div style={{ display: 'flex', gap: 12, marginTop: 8 }}>
              <Input
                value={taskTitle}
                onChange={setTaskTitle}
                placeholder="任务标题（可不填）"
                style={{ flex: 1 }}
              />
              <DatePicker
                type="dateTime"
                value={taskDue ?? undefined}
                onChange={(date) => setTaskDue((date as Date) ?? null)}
                placeholder="任务时间"
              />
            </div>
          )}
        </div>
      </div>
    </Modal>
  )
}
