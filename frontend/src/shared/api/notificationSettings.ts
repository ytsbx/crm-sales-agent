import { api } from './client'

/** 通知渠道设置（03-API §33 GET/PATCH /notification-settings）。 */

export interface NotificationSettings {
  inapp_enabled: boolean
  wecom_enabled: boolean
  wecom_events: {
    approval: boolean
    task: boolean
    payment: boolean
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
