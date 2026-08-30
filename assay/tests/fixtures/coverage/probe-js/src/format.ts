/**
 * Format a timestamp as a short relative time, e.g. "3m ago" / "in 2h".
 *
 * Accepts ISO strings (the API's default timestamp shape), epoch seconds or
 * milliseconds, or a Date. Returns "—" for empty or unparseable input so table
 * cells never render "Invalid Date".
 */
export function relativeTime(value: string | number | Date | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'

  // Numbers < 1e12 are almost certainly epoch *seconds*, not milliseconds.
  const date =
    value instanceof Date
      ? value
      : new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value)

  const ms = date.getTime()
  if (Number.isNaN(ms)) return '—'

  const diffSec = Math.round((ms - Date.now()) / 1000)
  const abs = Math.abs(diffSec)
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto', style: 'short' })

  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ['year', 31536000],
    ['month', 2592000],
    ['week', 604800],
    ['day', 86400],
    ['hour', 3600],
    ['minute', 60],
    ['second', 1],
  ]
  for (const [unit, secs] of units) {
    if (abs >= secs || unit === 'second') {
      return rtf.format(Math.round(diffSec / secs), unit)
    }
  }
  return '—'
}
