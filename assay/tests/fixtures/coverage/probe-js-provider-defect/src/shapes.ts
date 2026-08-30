// Five value-initialiser shapes, each in a function whose ONLY test call
// returns at the guard on the function's second line. Every line below that
// guard is therefore provably never executed, in all five functions.
export function ternaryMultiLine(v: number): number {
  if (v === 0) return 0
  const a =
    v > 3
      ? 10
      : 20
  const b = a + 2
  return b
}

export function ternaryOneLine(v: number): number {
  if (v === 0) return 0
  const a = v > 3 ? 10 : 20
  const b = a + 2
  return b
}

export function binaryMultiLine(v: number): number {
  if (v === 0) return 0
  const a =
    v +
    3
  const b = a + 2
  return b
}

export function callMultiLine(v: number): number {
  if (v === 0) return 0
  const a = Math.max(
    v,
    3,
  )
  const b = a + 2
  return b
}

export function objectLiteralMultiLine(v: number): number {
  if (v === 0) return 0
  const a = {
    x: v,
    y: 3,
  }
  const b = a.x + 2
  return b
}
