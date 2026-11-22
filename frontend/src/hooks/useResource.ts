import { useCallback, useContext, useEffect, useRef, useState } from 'react'
import { ApiError, errorMessage } from '../api/client'
import { ResourceCache, RESOURCE_STALE_AFTER_MS } from './resourceCache'

interface Resource<T> {
  data: T | null
  loading: boolean
  refreshing: boolean
  error: string
}

export function useResource<T>(
  load: (signal: AbortSignal) => Promise<T>,
  { key, enabled = true, staleAfterMs = RESOURCE_STALE_AFTER_MS, recheckOn }: {
    key?: string
    enabled?: boolean
    staleAfterMs?: number
    recheckOn?: string
  } = {},
) {
  const cache = useContext(ResourceCache)
  const active = useRef<AbortController | null>(null)
  const [revision, setRevision] = useState(0)
  const [state, setState] = useState<(Resource<T> & { key?: string }) | null>(null)
  const cached = key ? cache?.get(key) : undefined
  const snapshot = state && state.key === key
    ? state
    : { data: cached ? cached.data as T : null, loading: enabled && !cached, refreshing: false, error: '' }

  useEffect(() => {
    if (!enabled) return
    function check() {
      if (active.current || document.hidden) return
      const entry = key ? cache?.get(key) : undefined
      if (entry && Date.now() - entry.loadedAt < staleAfterMs) {
        setState((previous) => previous && previous.key === key && !previous.loading && !previous.refreshing && !previous.error
          ? previous
          : { key, data: entry.data as T, loading: false, refreshing: false, error: '' })
        return
      }
      const controller = new AbortController()
      active.current = controller
      const data = entry ? entry.data as T : null
      setState((previous) => ({
        key,
        data,
        loading: !entry,
        refreshing: Boolean(entry),
        error: previous && previous.key === key ? previous.error : '',
      }))
      void load(controller.signal)
        .then((result) => {
          if (controller.signal.aborted) return
          if (cache && key) remember(cache, key, result)
          setState({ key, data: result, loading: false, refreshing: false, error: '' })
        })
        .catch((failure: unknown) => {
          if (controller.signal.aborted) return
          const revoked = failure instanceof ApiError && [401, 403, 404].includes(failure.status)
          if (key && revoked) cache?.delete(key)
          setState({ key, data: revoked ? null : data, loading: false, refreshing: false, error: errorMessage(failure) })
        })
        .finally(() => {
          if (active.current === controller) active.current = null
        })
    }
    // Let Strict Mode discard its first effect before sending a request.
    const initialCheck = window.setTimeout(check, 0)
    if (key) document.addEventListener('visibilitychange', check)
    return () => {
      window.clearTimeout(initialCheck)
      document.removeEventListener('visibilitychange', check)
      active.current?.abort()
      active.current = null
    }
  }, [cache, enabled, key, load, recheckOn, revision, staleAfterMs])

  const reload = useCallback(() => {
    const entry = key ? cache?.get(key) : undefined
    if (entry) entry.loadedAt = 0
    setState((previous) => previous && previous.key === key && previous.error
      ? { ...previous, loading: !entry, refreshing: Boolean(entry) }
      : previous)
    setRevision((value) => value + 1)
  }, [cache, key])

  function replace(data: T) {
    active.current?.abort()
    active.current = null
    if (cache && key) remember(cache, key, data)
    setState({ key, data, loading: false, refreshing: false, error: '' })
  }

  function forget() {
    active.current?.abort()
    active.current = null
    if (key) cache?.delete(key)
  }

  return { ...snapshot, reload, replace, forget }
}

function remember(cache: NonNullable<React.ContextType<typeof ResourceCache>>, key: string, data: unknown) {
  cache.delete(key)
  cache.set(key, { data, loadedAt: Date.now() })
  // shortcut: bound entries per tab, use paged responses when collections outgrow a single payload.
  if (cache.size > 50) {
    const oldest = cache.keys().next()
    if (!oldest.done) cache.delete(oldest.value)
  }
}
