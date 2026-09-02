export function branchy(n: number): string {
  if (n < 0) {
    return 'negative'
  }
  if (n === 0) {
    return 'zero'
  }
  const label = n > 10 ? 'big' : 'small'
  return label
}
