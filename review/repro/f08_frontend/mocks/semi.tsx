import * as React from 'react'

/** Semi UI 只按工作台实际用到的交互面做最小替身；文案与结构原样保留。 */
export function Banner(props: { title?: React.ReactNode; description?: React.ReactNode }) {
  return <div data-banner="">{props.title}{props.description}</div>
}
export function Button(props: { children?: React.ReactNode; ariaLabel?: string; icon?: React.ReactNode }) {
  return <button type="button" aria-label={props.ariaLabel}>{props.icon}{props.children}</button>
}
export function Checkbox(props: { checked?: boolean; disabled?: boolean; ariaLabel?: string }) {
  return <input type="checkbox" readOnly checked={props.checked} disabled={props.disabled} aria-label={props.ariaLabel} />
}
export function Pagination() {
  return <div data-pagination="" />
}
export function Popconfirm(props: { children?: React.ReactNode }) {
  return <>{props.children}</>
}
export function SideSheet(props: { visible?: boolean; children?: React.ReactNode }) {
  return props.visible ? <div data-sidesheet="">{props.children}</div> : null
}
export function Tag(props: { children?: React.ReactNode }) {
  return <span data-tag="">{props.children}</span>
}
export const Toast = { success() {}, error() {}, warning() {}, info() {} }
export function Popover(props: { children?: React.ReactNode }) {
  return <>{props.children}</>
}
