import { createContext } from 'react'

export const RESOURCE_STALE_AFTER_MS = 5 * 60_000
export const REQUEST_STALE_AFTER_MS = 60_000
export const ResourceCache = createContext<Map<string, { data: unknown; loadedAt: number }> | null>(null)
