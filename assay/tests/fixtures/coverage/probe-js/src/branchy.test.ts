import { it, expect } from 'vitest'
import { branchy } from './branchy'

it('takes only the zero arm', () => {
  expect(branchy(0)).toBe('zero')
})
