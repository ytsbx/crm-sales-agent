import { oaFormFields } from './oa-form-fields'

export default function OaFormSnapshot({ result }: { result: unknown }) {
  const fields = oaFormFields(result)
  if (!fields.length) return null
  return <details style={{ marginTop: 12 }}>
    <summary style={{ cursor: 'pointer' }}>查看钉钉表单与回复资料</summary>
    <div style={{ display: 'grid', gap: 10, marginTop: 10 }}>
      {fields.map((field, index) => <div key={index}>
        <div style={{ fontWeight: 600 }}>{field.name}</div>
        <div style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{field.value}</div>
      </div>)}
    </div>
  </details>
}
