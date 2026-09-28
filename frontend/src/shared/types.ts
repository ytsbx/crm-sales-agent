/** 与后端约定的通用类型。 */

/** Semi Tag 可用的颜色取值（收敛成一个联合类型，避免各处写成任意 string）。 */
export type TagTone =
  | 'amber'
  | 'blue'
  | 'cyan'
  | 'green'
  | 'grey'
  | 'indigo'
  | 'lime'
  | 'orange'
  | 'pink'
  | 'purple'
  | 'red'
  | 'teal'
  | 'violet'
  | 'yellow'

export interface Envelope<T> {
  code: number
  message: string
  data: T
}

export interface PageResult<T> {
  items: T[]
  page: number
  page_size: number
  total: number
}

export interface LoginResult {
  access_token: string
  token_type: string
  user: { id: number; name: string; username: string }
}

export interface CurrentUser {
  id: number
  name: string
  username: string
  department?: string | null
  roles: string[]
  data_scope: string
  permissions?: string[]
}

export interface Product {
  id: number
  name: string
  product_line?: string | null
  category?: string | null
  brand?: string | null
  description?: string | null
  knowledge?: string | null
  status: string
  sku_count: number
  created_at: string
  updated_at: string
}

export interface Sku {
  id: number
  product_id: number
  product_name?: string | null
  sku_code: string
  name?: string | null
  specification?: string | null
  color?: string | null
  material?: string | null
  length?: number | null
  width?: number | null
  height?: number | null
  weight?: number | null
  carton_qty?: number | null
  carton_volume?: number | null
  moq?: number | null
  package_type?: string | null
  unit?: string | null
  status: string
  created_at: string
}

export interface Customer {
  id: number
  name: string
  short_name?: string | null
  customer_type?: string | null
  country?: string | null
  region?: string | null
  address?: string | null
  domain?: string | null
  tax_no?: string | null
  source?: string | null
  level?: string | null
  status: string
  pool_status: string
  owner_id?: number | null
  owner_name?: string | null
  contact_count: number
  /** 领导六阶段（自动推导：了解/报价/打样/首单/返单/稳定复购） */
  stage?: string | null
  stage_label?: string | null
  /** 客户标签（PRD §6.1 列表要展示标签） */
  tags?: { id: number; name: string; type: string; status: string }[]
  remark?: string | null
  last_followup_at?: string | null
  next_followup_at?: string | null
  created_at: string
  updated_at: string
}

export interface Contact {
  id: number
  customer_id?: number | null
  name: string
  title?: string | null
  department?: string | null
  mobile?: string | null
  phone?: string | null
  email?: string | null
  wechat?: string | null
  is_primary: boolean
  remark?: string | null
  created_at: string
}
