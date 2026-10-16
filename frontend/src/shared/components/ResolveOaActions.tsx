/**
 * 「结果待人工核对」的三个出口：认领 / 重发 / 作废。
 *
 * ## 为什么要有这个共用件
 *
 * 钉钉的发起接口**没有幂等键**。一次发起被超时/中断打断时，钉钉那边
 * **可能已经建了单**，于是只剩两条路：
 *
 *   · 自动重发 → 可能真建出第二张，本地只认一张，另一张成隐形挂单；
 *   · 什么都不做 → 单子永远发不出去，而发起人看不出来。
 *
 * 所以共享状态机定的口径是"卡住、转人工核对"，并给出三个动作
 * （`dingtalk/model.py` 的 `OA_STATUS_ACTIONS`）。**按钮只按服务端给的
 * `allowed_actions` 渲染**，前端不再自己判状态 —— 两边各写一套 if 必然分叉，
 * 分叉出去的那一侧就是重复建实例的入口。
 *
 * 询价侧（`KnowledgePage`）早就有这三个按钮，但合同与打样两条链一直没接，
 * 于是它们的单据一旦落到这个状态就**没有任何出口**：不能认领钉钉上可能已建出的
 * 那张单、不能重发、不能作废本轮，只能把整份单据作废重开 —— 而那恰恰是状态机
 * 要防的重复建单。这个组件就是把那段出口抽出来，让三条链共用同一份措辞。
 *
 * 注意：**驳回后重提不在这里**。重提是"再发一次正常发起"（合同走台账的
 * 「提交钉钉签署」、打样走弹窗的「修改后重新提交」），不是核定动作，
 * 放在这里会变成两个入口各发各的单。
 */
import { useState } from 'react'
import { useMutation, useQueryClient, type QueryKey } from '@tanstack/react-query'
import { Button, Input, Modal, Popconfirm, Toast } from '@douyinfe/semi-ui'
import { type OaApprovalInstance, resolveOaInstance } from '../api/dingtalk'
import { newRequestKey } from '../api/requestKey'

export function ResolveOaActions({
  row,
  invalidateKeys,
  onDone,
  onAdopt,
}: {
  row: OaApprovalInstance
  /** 这条记录影响到的查询键：处理完要刷新的那些。 */
  invalidateKeys: QueryKey[]
  onDone?: () => void
  /** 认领需要填钉钉单号，交由调用方的弹窗去收集（同一次核定共用一把请求键）。 */
  onAdopt: (row: OaApprovalInstance) => void
}) {
  const client = useQueryClient()
  const resolve = useMutation({
    mutationFn: ({
      action,
      requestKey,
    }: {
      action: 'resend' | 'abandon'
      requestKey: string
    }) =>
      resolveOaInstance(row.id, {
        action,
        note: action === 'abandon' ? '人工核对确认钉钉未建单，作废本轮' : undefined,
        // 同一次核定带同一个请求键：服务端据此保证"重发只生效一次并回放同一份结果"。
        // 每次点都是新的一次核定，所以这里每次都新生成一把。
        request_key: requestKey,
      }),
    onSuccess: (updated) => {
      Toast.success(`已处理：${updated.status_label}`)
      for (const key of invalidateKeys) void client.invalidateQueries({ queryKey: key })
      onDone?.()
    },
    onError: (error: Error) => Toast.error(error.message),
  })

  const actions = row.allowed_actions ?? []
  return (
    <>
      {actions.includes('resolve_adopt') && <a onClick={() => onAdopt(row)}>认领</a>}
      {actions.includes('resolve_resend') && (
        <Popconfirm
          title="确认钉钉那边没有这张单？"
          content="重发会再向钉钉发起一次；若其实已经建过，就会多出一张审批单。"
          onConfirm={() => resolve.mutate({ action: 'resend', requestKey: newRequestKey() })}
        >
          <a>重发</a>
        </Popconfirm>
      )}
      {actions.includes('resolve_abandon') && (
        <Popconfirm
          title="作废本轮记录？"
          content="仅在本系统里作废，不会动钉钉那边。"
          onConfirm={() => resolve.mutate({ action: 'abandon', requestKey: newRequestKey() })}
        >
          <a style={{ color: 'var(--crm-error)' }}>作废</a>
        </Popconfirm>
      )}
    </>
  )
}

/**
 * 认领弹窗：把钉钉上**已经存在**的那张审批单接过来。
 *
 * 为什么要人填单号、而不是系统自动找：钉钉的发起接口没有幂等键，也没有
 * "按业务号查实例"的可靠接口，所以只能由人去钉钉看一眼那单的真实编号。
 * 服务端会**先核实**模板/发起人/来源对象三项，任何一项对不上都拒绝采纳
 * （`dingtalk/service._verify_instance_source`，fail-closed）—— 输错一位不会
 * 变成"认了别人的单"。
 */
export function AdoptOaInstanceModal({
  target,
  requestKey,
  invalidateKeys,
  onClose,
}: {
  target: OaApprovalInstance | null
  /** 同一次核定的请求键：这个弹窗里的每次重试都复用它（换键＝告诉后端这是新的一次核定）。 */
  requestKey: string | null
  invalidateKeys: QueryKey[]
  onClose: () => void
}) {
  const client = useQueryClient()
  const [instanceId, setInstanceId] = useState('')
  const adopt = useMutation({
    mutationFn: () =>
      resolveOaInstance(target!.id, {
        action: 'adopt',
        instance_id: instanceId.trim(),
        request_key: requestKey ?? undefined,
      }),
    onSuccess: (updated) => {
      Toast.success(`已认领：${updated.status_label}`)
      setInstanceId('')
      for (const key of invalidateKeys) void client.invalidateQueries({ queryKey: key })
      onClose()
    },
    onError: (error: Error) => Toast.error(error.message),
  })
  if (!target) return null
  return (
    <Modal
      visible
      title="认领钉钉上已有的审批单"
      width={520}
      onCancel={() => {
        setInstanceId('')
        onClose()
      }}
      footer={
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
          <Button
            onClick={() => {
              setInstanceId('')
              onClose()
            }}
          >
            取消
          </Button>
          {/* 空单号不发请求：后端也会拦，但让用户点一个必然失败的按钮没意义 */}
          <Button
            theme="solid"
            disabled={!instanceId.trim() || adopt.isPending}
            loading={adopt.isPending}
            onClick={() => adopt.mutate()}
          >
            认领
          </Button>
        </div>
      }
    >
      <div style={{ marginBottom: 8 }}>
        请先到钉钉找到这张审批单，把它右上角的<strong>审批单编号</strong>填在下面。
        系统会核对模板、发起人和来源单据，对不上不会采纳。
      </div>
      <Input
        value={instanceId}
        placeholder="钉钉审批单编号"
        onChange={setInstanceId}
      />
    </Modal>
  )
}
