export const asMoney = (value: number) => `¥${Math.round(value).toLocaleString('zh-CN')}`
export const compactMoney = asMoney
export const comboBarLineOption = () => ({})
