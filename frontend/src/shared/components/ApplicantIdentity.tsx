import { Input } from '@douyinfe/semi-ui'
import { useAuthStore } from '../store/auth'
import FormLabel from './FormLabel'

export default function ApplicantIdentity({ prefill, sample = false }: { prefill?: { applicant_name: string; department: string }; sample?: boolean }) {
  const user = useAuthStore(state => state.user)
  /* 底色用站内的中性浅灰，不用淡蓝：同一屏里上方是白卡、下方是附件区，
     中间再插一块淡蓝，颜色就成了第三种"不知道什么意思"的底色。
     两块输入都是只读，灰底的语义正好是"系统带出来的，你别改"。 */
  return <section style={{ padding: 14, background: 'var(--crm-surface-low)', border: '1px solid var(--crm-border)', borderRadius: 10, minWidth: 0 }}>
    <div style={{ fontWeight: 600, marginBottom: 10 }}>提报信息 · 自动带入</div>
    {/* 两列在窄屏（461px）各只剩约 160px，姓名一长就截断；给一列的下限，
        放不下就上下叠，不硬挤成两条窄缝。 */}
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 }}>
      <div><FormLabel required>{sample ? '申请人员' : '提报人'}</FormLabel><Input readOnly value={prefill?.applicant_name ?? user?.name ?? ''} /></div>
      <div><FormLabel required>{sample ? '归属部门' : '提报部门'}</FormLabel><Input readOnly value={prefill?.department ?? user?.department ?? ''} placeholder="请先在账号资料中设置部门" /></div>
    </div>
  </section>
}
