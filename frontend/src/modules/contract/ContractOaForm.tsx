import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Cascader, Input, Select, TextArea, Toast } from '@douyinfe/semi-ui'
import { api } from '../../shared/api/client'
import { uploadFile } from '../../shared/api/file'

interface Field {
  id: string; label: string; type: string; required?: boolean; locked?: boolean; readonly?: boolean
  options?: string[]; multiline?: boolean; help_text?: string; presentation?: string
  visible_when?: { field: string; values: string[] }
  editable_contact?: boolean
  columns?: { id: string; label: string; required?: boolean }[]
}
function categoryTree(options: string[]) {
  const roots: { value: string; label: string; children?: { value: string; label: string }[] }[] = []
  for (const option of options) {
    const [group, name] = option.split(' / ')
    if (!name) {
      roots.push({ value: group, label: group })
      continue
    }
    let parent = roots.find(root => root.value === group)
    if (!parent) {
      parent = { value: group, label: group, children: [] }
      roots.push(parent)
    }
    parent.children?.push({ value: name, label: name })
  }
  return roots
}
interface Definition { fields: Field[]; defaults: Record<string, unknown>; people: { id: string; name: string }[] }
export default function ContractOaForm({ docId, values, onChange, onBusy }: { docId: number; values: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void; onBusy: (busy: boolean) => void }) {
  const query = useQuery({ queryKey: ['contract-oa-form', docId], queryFn: () => api.get<Definition>(`/contract-documents/${docId}/oa-form`) })
  const [uploading, setUploading] = useState(false)
  const initialized = useRef(false)
  useEffect(() => {
    if (!query.data || initialized.current) return
    initialized.current = true
    onChange(Object.fromEntries(query.data.fields.filter(field => !field.locked && query.data.defaults[field.id] !== undefined)
      .map(field => [field.id, query.data.defaults[field.id]])))
  }, [query.data, onChange])
  useEffect(() => { onBusy(query.isPending || query.isError || uploading) }, [query.isPending, query.isError, uploading, onBusy])
  if (query.isPending) return <div>正在读取同序合同表单…</div>
  if (query.isError) return <div role="alert">{query.error.message}</div>
  const data = query.data
  const set = (id: string, value: unknown) => onChange({ ...values, [id]: value })
  const display = (v: unknown) => Array.isArray(v) ? v.map(row => typeof row === 'object' && row ? Object.values(row).join(' · ') : String(row)).join('；') : String(v ?? '')
  const fieldValues = { ...data.defaults, ...values }
  const definitions = new Map(data.fields.map((field) => [field.id, field]))
  function visible(field: Field, trail = new Set<string>()): boolean {
    const rule = field.visible_when
    if (!rule) return true
    if (trail.has(field.id)) return false
    const parent = definitions.get(rule.field)
    return Boolean(parent && visible(parent, new Set([...trail, field.id])) &&
      rule.values.includes(String(fieldValues[rule.field] ?? '')))
  }
  return <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 260px), 1fr))', gap: 16 }}>
    {data.fields.filter(field => visible(field) && !['contract_biz_id', 'external_ref', 'request_id', 'trace_id'].includes(field.id)).map(field => {
      const value = values[field.id] ?? data.defaults[field.id] ?? ''
      const wide = ['attachment', 'table'].includes(field.type) || field.multiline
      return <div key={field.id} style={wide ? { gridColumn: '1 / -1' } : undefined}>
        <div style={{ fontWeight: 500, marginBottom: 7 }}>{field.label}{field.required && <span style={{ color: 'var(--crm-error)' }}> *</span>}</div>
        {field.type === 'table' && field.columns && Array.isArray(value) ? <div style={{ display: 'grid', gap: 12 }}>
          {value.map((row, index) => <div key={index} style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12 }}>
            {field.columns!.map(column => <label key={column.id} style={{ display: 'grid', gap: 5 }}>
              {column.label}{column.required ? ' *' : ''}
              <Input readOnly={!field.editable_contact || column.id !== 'contact'}
                value={String(row[column.id] ?? '')}
                onChange={contact => set(field.id, value.map((party, rowIndex) => rowIndex === index ? { ...party, contact } : party))} />
            </label>)}
          </div>)}
        </div>
          : field.locked || field.readonly ? <Input readOnly value={display(value)} placeholder={field.type === 'serial' ? '提交后由同序自动生成' : '系统自动带入'} />
          : field.type === 'attachment' ? <div>
              <input type="file" multiple disabled={uploading} onChange={async event => {
                const files = Array.from(event.target.files ?? [])
                const ids: number[] = Array.isArray(value) ? value as number[] : []
                setUploading(true)
                try {
                  const added = await Promise.all(files.map(file => uploadFile(file, { businessType: 'contract', businessId: docId, category: field.id })))
                  set(field.id, [...ids, ...added.map(row => row.id)])
                } catch (error) { Toast.error(error instanceof Error ? error.message : '上传失败') }
                finally { setUploading(false) }
              }} />
              <div style={{ fontSize: 12, color: 'var(--crm-text-3)', marginTop: 6 }}>已选 {Array.isArray(value) ? value.length : 0} 个附件</div>
            </div>
          : field.presentation === 'cascade' ? <Cascader style={{ width: '100%' }} showClear
              treeData={categoryTree(field.options ?? [])} value={typeof value === 'string' && value ? value.split(' / ') : []}
              onChange={selected => set(field.id, Array.isArray(selected) ? selected.join(' / ') : selected ?? '')} />
          : ['choice', 'multi_choice', 'user'].includes(field.type) ? <Select style={{ width: '100%' }} multiple={field.type === 'multi_choice'} showClear
              value={value || undefined} onChange={v => set(field.id, v ?? '')}
              optionList={field.type === 'user' ? data.people.map(p => ({ value: p.id, label: p.name })) : (field.options ?? []).map(v => ({ value: v, label: v }))} />
          : field.type === 'date' ? <input type="date" style={{ width: '100%', padding: 8, border: '1px solid var(--crm-border)', borderRadius: 6 }} value={String(value)} onChange={e => set(field.id, e.target.value)} />
          : field.multiline || field.id === 'contract_description' ? <TextArea value={String(value)} onChange={v => set(field.id, v)} />
          : <Input value={String(value)} onChange={v => set(field.id, v)} />}
        {field.help_text && <small style={{ color: 'var(--crm-text-3)', whiteSpace: 'pre-line' }}>{field.help_text}</small>}
      </div>
    })}
  </div>
}
