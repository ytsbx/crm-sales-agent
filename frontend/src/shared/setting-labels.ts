/**
 * 系统配置的用户可见中文名与说明。
 *
 * 这份表和后端 DEFAULT_SETTINGS 一一对应；ops/check_setting_labels.py
 * 会在 CI 中检查新增配置没有漏翻译。
 */
export const SETTING_LABELS: Record<string, { label: string; description: string }> = {
  company_name: { label: '公司名称', description: '报价单和业务单据上显示的公司全称。' },
  quote_valid_days: { label: '报价有效期', description: '新报价默认有效天数。' },
  default_payment_terms: { label: '默认付款条件', description: '新报价默认使用的付款条件。' },
  default_delivery_terms: { label: '默认交货条款', description: '新报价默认使用的交货与运费说明。' },
  default_target_margin: { label: '默认目标利润率', description: '核价未指定目标利润率时使用的比例。' },
  default_min_margin: { label: '默认最低利润率', description: '没有角色专属规则时使用的最低利润率。' },
  price_range_ratio: { label: '建议报价浮动范围', description: '建议报价上下浮动的比例。' },
  hard_floor: { label: '绝对底价规则', description: '低于该规则计算出的底价时直接拒绝报价。' },
  export: { label: '导出限制', description: '单次导出上限和异常批量导出告警阈值。' },
  notification_retry: { label: '通知重试', description: '通知失败后的自动重试次数与间隔。' },
  logistics_volumetric_ratio: { label: '物流体积重系数', description: '按体积换算计费重量的系数，0 表示不启用。' },
  customer_stale_days: { label: '客户沉睡天数', description: '超过此天数没有有效跟进时标记为沉睡。' },
  customer_active_days: { label: '客户活跃天数', description: '最近多少天内有跟进算作活跃客户。' },
  opportunity_risk_days: { label: '商机风险天数', description: '距离预计成交日小于此天数时提示风险。' },
  opportunity_stale_days: { label: '商机停滞天数', description: '超过此天数没有推进时标记为停滞。' },
  approval_levels: { label: '报价审批分级', description: '按报价金额决定审批级别和可审批角色。' },
  dedup_scoring: { label: '客户查重规则', description: '客户重复识别的各项权重与命中阈值。' },
  trade_mode: { label: '贸易模式', description: '保留历史贸易模式配置；当前核价只支持人民币。' },
  default_currency: { label: '默认币种', description: '系统默认使用的币种。' },
  export_tax_refund_rate: { label: '出口退税率', description: '历史出口数据使用的默认退税率。' },
  pool_recycle_notice_days: { label: '公海回收预告期', description: '客户进入待复核前的预告天数。' },
  pool_recycle_defer_days: { label: '公海回收暂缓期', description: '主管暂缓回收后再次可复核前的等待天数。' },
  pool_recycle_restore_permission: { label: '公海回收恢复权限', description: '允许把已回收客户恢复的权限。' },
  notification_channels: { label: '通知渠道', description: '站内通知和企业微信通知的开关与适用类型。' },
  notification_levels: { label: '通知分级', description: '不同通知类型采用即时发送或日报汇总。' },
  dingtalk_oa: { label: '钉钉审批配置', description: '钉钉审批模板、字段映射和发起人来源。' },
}

export function settingLabel(key: string): string {
  return SETTING_LABELS[key]?.label ?? key
}

export function settingDescription(key: string, fallback?: string | null): string {
  return SETTING_LABELS[key]?.description ?? fallback ?? '暂无说明'
}
