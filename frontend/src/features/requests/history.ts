import { API_BASE_URL } from '../../config'

const storageKey = `knowledge-desk.request-ids.v1:${API_BASE_URL}`
const historyEvent = 'knowledge-desk-request-history'

export function readRequestIds(): string[] {
  try {
    const ids: unknown = JSON.parse(localStorage.getItem(storageKey) || '[]')
    return Array.isArray(ids)
      ? [
          ...new Set(
            ids.filter(
              (id): id is string =>
                typeof id === 'string' && /^[a-f0-9-]{36}$/i.test(id),
            ),
          ),
        ]
      : []
  } catch {
    return []
  }
}

export function saveRequestIds(ids: string[]): boolean {
  try {
    localStorage.setItem(storageKey, JSON.stringify(ids))
    window.dispatchEvent(new Event(historyEvent))
    return true
  } catch {
    return false
  }
}

export function watchRequestIds(onChange: () => void) {
  window.addEventListener(historyEvent, onChange)
  return () => window.removeEventListener(historyEvent, onChange)
}

type RequestPreference = 'view' | 'search'

export function readRequestPreference(preference: RequestPreference): string {
  try {
    return localStorage.getItem(`knowledge-desk.request-${preference}.v1:${API_BASE_URL}`) ?? ''
  } catch {
    return ''
  }
}

export function saveRequestPreference(preference: RequestPreference, value: string) {
  try {
    const key = `knowledge-desk.request-${preference}.v1:${API_BASE_URL}`
    if (value) localStorage.setItem(key, value)
    else localStorage.removeItem(key)
  } catch {
    // The current page still works when storage is unavailable.
  }
}
