export function hinted(n: number): string {
  /* istanbul ignore next */
  if (n < 0) {
    return 'negative'
  }
  return 'ok'
}
