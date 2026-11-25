import * as React from 'react'
/** PageHeader / SectionCard / GroupHead / MaskedContact 的通用替身：保留标题与内容。 */
export default function Slot(props: { title?: React.ReactNode; subtitle?: React.ReactNode; extra?: React.ReactNode; note?: React.ReactNode; value?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div data-slot="">
      {props.title}{props.subtitle}{props.note}{props.extra}{props.value}{props.children}
    </div>
  )
}
