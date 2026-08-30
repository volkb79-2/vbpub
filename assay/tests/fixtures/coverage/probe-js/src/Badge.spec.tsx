import { describe, it, expect } from 'vitest'
import { Badge } from './Badge'

describe('Badge', () => {
  it('is declared but never rendered', () => {
    expect(typeof Badge).toBe('function')
  })
})
