import { useId, useState } from 'react'
import { Popover } from '@douyinfe/semi-ui'

export default function HelpTip({ forLabel, text }: { forLabel: string; text: string }) {
  const id = useId()
  const [mode, setMode] = useState<'hover' | 'pinned' | null>(null)

  return (
    <Popover
      trigger="custom"
      visible={mode !== null}
      position="top"
      showArrow
      closeOnEsc
      onClickOutSide={() => setMode(null)}
      onVisibleChange={visible => { if (!visible) setMode(null) }}
      aria-label={`${forLabel}说明`}
      content={<div id={id} className="crm-help-tip-content">{text}</div>}
    >
      <button
        type="button"
        className="crm-help-tip"
        aria-label={`${forLabel}说明`}
        aria-expanded={mode !== null}
        aria-describedby={mode !== null ? id : undefined}
        onPointerEnter={event => {
          if (event.pointerType === 'mouse') setMode(current => current ?? 'hover')
        }}
        onPointerLeave={() => setMode(current => current === 'hover' ? null : current)}
        onClick={event => {
          event.stopPropagation()
          setMode(current => current === 'pinned' ? null : 'pinned')
        }}
        onKeyDown={event => {
          if (event.key === 'Escape' && mode) {
            event.stopPropagation()
            setMode(null)
          }
        }}
      >
        <span aria-hidden="true">?</span>
      </button>
    </Popover>
  )
}
