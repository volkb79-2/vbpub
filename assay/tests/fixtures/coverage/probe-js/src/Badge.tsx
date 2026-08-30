import type { UserRole } from './types'

export interface BadgeProps {
  role: UserRole
  label?: string
}

export function Badge({ role, label }: BadgeProps) {
  const text =
    label ??
    (role === 'admin'
      ? 'Administrator'
      : 'Member')
  return (
    <span className={`badge badge--${role}`}>
      {text}
    </span>
  )
}

export function neverCalled(value: number): number {
  const doubled = value * 2
  return doubled
}
