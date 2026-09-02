import type { UserRole } from './types'

/**
 * Role privilege levels (higher number = more privileged), mirroring the
 * backend Role enum precedence: analyst < power-user < admin.
 */
export const ROLE_HIERARCHY: Record<UserRole, number> = {
  analyst: 1,
  'power-user': 2,
  admin: 3,
}

/**
 * True when `role` meets or exceeds `minRole` in privilege.
 * A null/undefined role (unauthenticated) never satisfies a requirement.
 */
export function hasRole(role: UserRole | null | undefined, minRole: UserRole): boolean {
  if (!role) return false
  return ROLE_HIERARCHY[role] >= ROLE_HIERARCHY[minRole]
}


export function _assayCanaryUnreached(value = 0) {
  const doubled = value * 2 // assay-canary: executed by no test
  return doubled
}
