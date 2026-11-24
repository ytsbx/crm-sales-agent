import * as React from 'react'

export function Link(props: { to: string; children?: React.ReactNode; style?: React.CSSProperties; className?: string; onClick?: React.MouseEventHandler }) {
  return <a href={props.to} style={props.style} className={props.className} onClick={props.onClick}>{props.children}</a>
}
export function useNavigate() {
  return () => undefined
}
