import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Input, Select, TextArea, Toast } from '@douyinfe/semi-ui'
import { api } from '../../shared/api/client'
import { uploadFile } from '../../shared/api/file'

interface Field { id: string; label: string; type: string; required?: boolean; locked?: boolean; options?: string[]; multiline?: boolean }
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
  return <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 16 }}>
    {data.fields.filter(field => !['contract_biz_id', 'external_ref', 'request_id', 'trace_id'].includes(field.id)).map(field => {
      const value = values[field.id] ?? data.defaults[field.id] ?? ''
      const wide = ['attachment', 'table'].includes(field.type) || field.multiline
      return <div key={field.id} style={wide ? { gridColumn: '1 / -1' } : undefined}>
        <div style={{ fontWeight: 500, marginBottom: 7 }}>{field.label}{field.required && <span style={{ color: 'var(--crm-error)' }}> *</span>}</div>
        {field.locked ? <Input readOnly value={display(value)} placeholder="系统自动带入" />
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
          : ['choice', 'multi_choice', 'user'].includes(field.type) ? <Select style={{ width: '100%' }} multiple={field.type === 'multi_choice'} showClear
              value={value || undefined} onChange={v => set(field.id, v ?? '')}
              optionList={field.type === 'user' ? data.people.map(p => ({ value: p.id, label: p.name })) : (field.options ?? []).map(v => ({ value: v, label: v }))} />
          : field.type === 'date' ? <input type="date" style={{ width: '100%', padding: 8, border: '1px solid var(--crm-border)', borderRadius: 6 }} value={String(value)} onChange={e => set(field.id, e.target.value)} />
          : field.multiline || field.id === 'contract_description' ? <TextArea value={String(value)} onChange={v => set(field.id, v)} />
          : <Input value={String(value)} onChange={v => set(field.id, v)} />}
      </div>
    })}
  </div>
}
