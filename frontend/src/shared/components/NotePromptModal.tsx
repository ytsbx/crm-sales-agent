import { useRef, useState } from 'react'
import { Button, Modal, TextArea } from '@douyinfe/semi-ui'

/* 填一句说明的弹窗（2026-11-19）。
   ------------------------------------------------------------
   原来这类"填原因"全靠 window.prompt()：浏览器自带的灰框、顶上一行
   "127.0.0.1:5275 显示"，没法设样式、没法做必填提示、也没法解释为什么问。
   暂缓回收、提前回收、恢复负责人、改来源编码……全站共有 7 处，看着都一样廉价。

   现在统一成这个：标题写"要你做什么"，说明写"这句话会去哪"，
   输入框带字数、必填没填时在下面用红字点出来，确认键是实心主色。
   用法是 await —— 填了返回文本，取消/关掉返回 null：

     const notePrompt = useNotePrompt()
     const note = await notePrompt.ask({ title: '暂缓回收', required: true })
     ...
     {notePrompt.node}
*/

export type NotePromptOptions = {
  /** 弹窗标题：写动作，不写"请输入" */
  title: string
  /** 标题下面那段说明：为什么问、这句话会去哪 */
  description?: string
  /** 输入框上方的小标签 */
  label?: string
  placeholder?: string
  defaultValue?: string
  /** 必填：没填就不让提交，红字提示 */
  required?: boolean
  maxLength?: number
  okText?: string
  /** warning/danger 时头部图标与确认键换成风险色 */
  tone?: 'normal' | 'warning' | 'danger'
}

const DEFAULT_MAX = 300

export function useNotePrompt() {
  const [options, setOptions] = useState<NotePromptOptions | null>(null)
  const [value, setValue] = useState('')
  const [error, setError] = useState<string | null>(null)
  const resolver = useRef<((result: string | null) => void) | null>(null)

  /** 打开弹窗，等用户填完；取消/关闭回 null */
  const ask = (next: NotePromptOptions) =>
    new Promise<string | null>((resolve) => {
      resolver.current = resolve
      setOptions(next)
      setValue(next.defaultValue ?? '')
      setError(null)
    })

  const settle = (result: string | null) => {
    resolver.current?.(result)
    resolver.current = null
    setOptions(null)
    setError(null)
  }

  const submit = () => {
    if (!options) return
    const trimmed = value.trim()
    if (options.required && !trimmed) {
      setError(`请填写${options.label ?? '这段说明'}`)
      return
    }
    settle(trimmed)
  }

  const maxLength = options?.maxLength ?? DEFAULT_MAX
  const tone = options?.tone ?? 'normal'

  const node = options ? (
    <Modal
      visible
      width={480}
      className="crm-confirm-modal note-prompt-modal"
      style={{ maxWidth: 'calc(100vw - 48px)' }}
      onCancel={() => settle(null)}
      footer={
        <>
          <Button onClick={() => settle(null)}>取消</Button>
          <Button
            type="primary"
            /* 确认键一律实心：warning 只是"这句原因要讲清楚代价"，不是"按钮也是次级的"。
               原来 warning 用 light，两个按钮都变成浅底，主次直接没了（实拍）。 */
            theme={tone === 'danger' ? 'light' : 'solid'}
            className={tone === 'danger' ? 'note-prompt-danger-btn' : undefined}
            onClick={submit}
          >
            {options.okText ?? '确认'}
          </Button>
        </>
      }
      header={
        <div className="crm-confirm-header">
          <span
            className={`crm-confirm-icon${tone === 'warning' ? ' is-warning' : ''}${tone === 'danger' ? ' is-danger' : ''}`}
            aria-hidden="true"
          >
            ✎
          </span>
          <span className="crm-confirm-title">{options.title}</span>
          {/* 右上角只放一句"这句话的归宿"，不再另起一行占高度 */}
          <span className="crm-confirm-sub">会记入审计记录</span>
        </div>
      }
    >
      <div className="crm-confirm-body note-prompt-body">
        {options.description && <p className="crm-confirm-lead">{options.description}</p>}
        <div className="note-prompt-field">
          <label htmlFor="note-prompt-input">{options.label ?? '说明'}</label>
          <TextArea
            id="note-prompt-input"
            value={value}
            maxLength={maxLength}
            placeholder={options.placeholder ?? '写清楚原因，之后在审计记录里能看到'}
            autoFocus
            onChange={(next) => {
              setValue(next)
              if (error) setError(null)
            }}
            onKeyDown={(event) => {
              // Ctrl/Cmd + Enter 直接提交：这框常只有一行话，不必再挪鼠标
              if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') submit()
            }}
          />
          <div className="note-prompt-meta">
            <span className={error ? 'note-prompt-error' : undefined}>
              {error ?? (options.required ? '必填，写清原因后才能提交' : '选填')}
            </span>
            <span>
              {value.length}/{maxLength}
            </span>
          </div>
        </div>
      </div>
    </Modal>
  ) : null

  return { ask, node }
}
