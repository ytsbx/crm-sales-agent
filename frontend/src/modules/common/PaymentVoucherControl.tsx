import { useRef, useState } from 'react'
import { Button, Popconfirm, Toast } from '@douyinfe/semi-ui'

import {
  deletePaymentVoucher,
  downloadPaymentVoucher,
  uploadPaymentVoucher,
  type Payment,
} from '../../shared/api/order'
import { usePermissions } from '../../shared/hooks/permissions'

interface Props {
  payment: Payment
  onChanged?: () => void
}

export default function PaymentVoucherControl({ payment, onChanged }: Props) {
  const { can } = usePermissions()
  const inputRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const filename = payment.voucher_file_name || `回款凭证-${payment.id}`
  const canManage = can('payment:manage') && payment.status === 'pending'

  const upload = async (file: File) => {
    setBusy(true)
    try {
      await uploadPaymentVoucher(payment.id, file)
      Toast.success('回款凭证已上传')
      onChanged?.()
    } catch (error) {
      Toast.error(error instanceof Error ? error.message : '凭证上传失败')
    } finally {
      setBusy(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  const remove = async () => {
    setBusy(true)
    try {
      await deletePaymentVoucher(payment.id)
      Toast.success('回款凭证已删除')
      onChanged?.()
    } catch (error) {
      Toast.error(error instanceof Error ? error.message : '凭证删除失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
      {payment.voucher_file_id ? (
        <>
          <a
            style={{ color: 'var(--crm-primary)' }}
            onClick={() => void downloadPaymentVoucher(payment.id, filename)}
          >
            {filename}
          </a>
          {canManage && (
            <Popconfirm title="删除这张待确认回款的凭证？" onConfirm={() => void remove()}>
              <a style={{ color: 'var(--crm-error)' }}>删除</a>
            </Popconfirm>
          )}
        </>
      ) : canManage ? (
        <>
          <input
            ref={inputRef}
            type="file"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) void upload(file)
            }}
          />
          <Button size="small" loading={busy} onClick={() => inputRef.current?.click()}>
            上传凭证
          </Button>
        </>
      ) : (
        '-'
      )}
    </div>
  )
}
