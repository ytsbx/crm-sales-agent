/** 面向业务人员：名称、规格优先；编码仅辅助区分及搜索。 */
export function skuLabel(sku: {
  product_name?: string | null
  name?: string | null
  specification?: string | null
  sku_code?: string | null
}): string {
  const parts = [...new Set([sku.product_name, sku.name, sku.specification]
    .map((value) => value?.trim()).filter(Boolean))]
  const title = parts.join(' · ') || '未命名产品'
  return sku.sku_code ? `${title}（编码：${sku.sku_code}）` : title
}
