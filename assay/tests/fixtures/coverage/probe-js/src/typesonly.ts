import type { Pageable } from './types'

export interface Widget {
  id: string
  page: Pageable
}

export type WidgetList = Widget[]
