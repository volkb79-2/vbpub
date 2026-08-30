import { it, expect } from 'vitest'
import { hinted } from './hinted'

it('never takes the hinted arm', () => {
  expect(hinted(1)).toBe('ok')
})
