import type { ReactNode } from 'react'

export default function DetailField({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <div style={{ color: 'var(--crm-text-3)', fontSize: 13, marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 14 }}>{value ?? '-'}</div>
    </div>
  )
}
