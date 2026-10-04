import { useState } from 'react'
import { Button, Modal } from '@douyinfe/semi-ui'

import { usePermissions } from '../../shared/hooks/permissions'
import AttachmentPanel from './AttachmentPanel'

export default function FollowUpAttachmentsButton({ followupId }: { followupId: number }) {
  const { can } = usePermissions()
  const [visible, setVisible] = useState(false)

  if (!can('file:view')) return null

  return (
    <>
      <Button size="small" onClick={() => setVisible(true)}>附件</Button>
      <Modal
        title="跟进附件"
        visible={visible}
        onCancel={() => setVisible(false)}
        footer={null}
        width={760}
      >
        <AttachmentPanel businessType="followup" businessId={followupId} enabled={visible} />
      </Modal>
    </>
  )
}
