import { it, expect } from 'vitest'
import {
  ternaryMultiLine,
  ternaryOneLine,
  binaryMultiLine,
  callMultiLine,
  objectLiteralMultiLine,
} from './shapes'

it('takes only the guard arm of every function', () => {
  expect(ternaryMultiLine(0)).toBe(0)
  expect(ternaryOneLine(0)).toBe(0)
  expect(binaryMultiLine(0)).toBe(0)
  expect(callMultiLine(0)).toBe(0)
  expect(objectLiteralMultiLine(0)).toBe(0)
})
