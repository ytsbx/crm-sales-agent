/** 筛选/规则用 UNRATED；写入客户档案时转换为 null。 */
export const UNRATED = 'UNRATED'
export const UNRATED_OPTION = { value: UNRATED, label: '未分级' }
export const customerLevelValue = (value?: string | null) => value || UNRATED
export const customerLevelPayload = (value: unknown) => value === UNRATED || !value ? null : String(value)
export const customerLevelLabel = (value?: string | null) => !value || value === UNRATED ? '未分级' : `${value} 级`
