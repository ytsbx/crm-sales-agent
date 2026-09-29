import { api } from './client'

/** 通知渠道设置（03-API §33 GET/PATCH /notification-settings）。 */

export interface NotificationSettings {
  inapp_enabled: boolean
  wecom_enabled: boolean
  wecom_events: {
    approval: boolean
    task: boolean
    payment: boolean
    /** 业务动作自动留痕推业务主管（报价提交 / 打样 / 下单）。 */
    followup: boolean
  }
  /** 后端企微对接是否就绪（用来提示"开了渠道但还没配密钥"）。 */
  wecom_ready: boolean
  wecom_agent_configured: boolean
}

export function getNotificationSettings() {
  return api.get<NotificationSettings>('/notification-settings')
}

export function updateNotificationSettings(payload: Partial<NotificationSettings>) {
  return api.patch<NotificationSettings>('/notification-settings', payload)
}

// ---------------------------------------------------------------- 通知分级（场景24）
// 文档 §11.4：「按最终批准的**逐次或分级策略**投递，紧急项不被日报延误」。
// 默认是"全部即时推"（与分级上线前一致）——**不配置就不改变任何投递行为**，
// 哪些类型该进日报属于业务决策，所以做成可配而不是代码写死。

export interface NotificationLevelPolicy {
  default_level: string
  by_type: Record<string, string>
  levels: { value: string; label: string }[]
}

export function getLevelPolicy() {
  return api.get<NotificationLevelPolicy>('/notifications/level-policy')
}

export function updateLevelPolicy(payload: {
  default_level: string
  by_type: Record<string, string>
}) {
  return api.put<{ default_level: string; by_type: Record<string, string> }>(
    '/notifications/level-policy',
    payload,
  )
}
