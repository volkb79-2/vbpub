import { describe, it, expect } from 'vitest'
import type { WidgetList } from '../typesonly'
import { hasRole } from '../roles'
import { relativeTime } from '../format'

describe('hasRole', () => {
  it('respects the hierarchy', () => {
    expect(hasRole('admin', 'analyst')).toBe(true)
    expect(hasRole(null, 'analyst')).toBe(false)
  })
})

describe('relativeTime', () => {
  it('renders an em dash for empty input', () => {
    expect(relativeTime('')).toBe('—')
  })
})

describe('typesonly', () => {
  it('is a type-only module', () => {
    const list: WidgetList = []
    expect(list.length).toBe(0)
  })
})
