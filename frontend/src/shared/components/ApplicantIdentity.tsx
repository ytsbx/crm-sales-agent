import { Input } from '@douyinfe/semi-ui'
import { useAuthStore } from '../store/auth'
import FormLabel from './FormLabel'

export default function ApplicantIdentity({ prefill, sample = false }: { prefill?: { applicant_name: string; department: string }; sample?: boolean }) {
  const user = useAuthStore(state => state.user)
  return <section style={{ padding: 14, background: '#f7f9fc', border: '1px solid #e8edf4', borderRadius: 10 }}>
    <div style={{ fontWeight: 600, marginBottom: 10 }}>提报信息 · 自动带入</div>
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 12 }}>
      <div><FormLabel required>{sample ? '申请人员' : '提报人'}</FormLabel><Input readOnly value={prefill?.applicant_name ?? user?.name ?? ''} /></div>
      <div><FormLabel required>{sample ? '归属部门' : '提报部门'}</FormLabel><Input readOnly value={prefill?.department ?? user?.department ?? ''} placeholder="请先在账号资料中设置部门" /></div>
    </div>
  </section>
}
