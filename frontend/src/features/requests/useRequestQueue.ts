import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { watchCertificateUpdates } from '../../api/certificates'
import { getRequest, listRequests } from '../../api/requests'
import { isObject, type RequestRecord, type RequestStatus } from '../../api/types'
import { REQUEST_STALE_AFTER_MS, ResourceCache } from '../../hooks/resourceCache'
import { useResource } from '../../hooks/useResource'
import { readHash, requestsHref } from '../../navigation/routes'
import { readRequestIds, readRequestPreference, saveRequestIds, saveRequestPreference, watchRequestIds } from './history'

export const statusOptions = {
  all: 'Все статусы',
  new: 'Новые',
  in_progress: 'В работе',
  awaiting_employee: 'Ждут ответа',
  resolved: 'Закрытые',
} satisfies Record<'all' | RequestStatus | 'awaiting_employee', string>

function useRequestHistory(ids: string[], visible: boolean, remote: boolean) {
  const load = useCallback(
    async (signal: AbortSignal): Promise<PromiseSettledResult<RequestRecord>[]> => remote
      ? (await listRequests(signal)).map((value) => ({ status: 'fulfilled', value }))
      : Promise.allSettled(ids.map((id) => getRequest(id, signal))),
    [ids, remote],
  )
  const resource = useResource(load, {
    key: remote ? 'requests' : `requests:${ids.join(',')}`,
    enabled: visible,
    staleAfterMs: REQUEST_STALE_AFTER_MS,
  })
  const [today, setToday] = useState(() => new Date().toDateString())
  useEffect(() => {
    const midnight = new Date()
    midnight.setHours(24, 0, 0, 0)
    const timer = window.setTimeout(() => setToday(new Date().toDateString()), midnight.getTime() - Date.now())
    return () => window.clearTimeout(timer)
  }, [today])
  return { ...resource, today }
}

function matchRequests(data: PromiseSettledResult<RequestRecord>[] | null, filter: keyof typeof statusOptions, query: string, oldestFirst: boolean) {
  const term = query.trim()
  const pattern = term ? new RegExp(term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'iu') : null
  const matches = data?.flatMap((result) => {
    if (result.status !== 'fulfilled') return []
    const record = result.value
    const status = record.certificate_awaiting_reply ? 'awaiting_employee' : record.status
    if (filter !== 'all' && status !== filter) return []
    const questionMatch = pattern?.exec(record.question)
    const reply = pattern && !questionMatch
      ? record.replies?.find((entry) => pattern.test(entry.text))
      : undefined
    if (pattern && !questionMatch && !reply) return []
    return [{
      record,
      sentAt: record.created_at ? Date.parse(record.created_at) : null,
      questionMatch,
      replyMatch: reply ? pattern?.exec(reply.text) : undefined,
    }]
  }) ?? []
  matches.sort((a, b) => {
    if (a.sentAt === b.sentAt) return 0
    if (a.sentAt === null) return 1
    if (b.sentAt === null) return -1
    return oldestFirst ? a.sentAt - b.sentAt : b.sentAt - a.sentAt
  })
  return matches
}

function invalidateCertificateCards(cache: React.ContextType<typeof ResourceCache>) {
  for (const [key, entry] of cache ?? []) {
    if (key.startsWith('request:') && isObject(entry.data) && entry.data.kind === 'employment_certificate') {
      entry.loadedAt = 0
    }
  }
}

export function useRequestQueue({ visible, remote, watchQueue, ownCertificateRevision, statusFilter, sortOrder }: {
  visible: boolean
  remote: boolean
  watchQueue: boolean
  ownCertificateRevision?: string
  statusFilter: string | null
  sortOrder: string | null
}) {
  const [ids, setIds] = useState(readRequestIds)
  const [query, setQuery] = useState(() => readRequestPreference('search'))
  const [storageError, setStorageError] = useState(false)
  const filter = Object.keys(statusOptions).find((value) => value === statusFilter) as keyof typeof statusOptions | undefined
  const activeFilter = filter ?? 'all'
  const oldestFirst = sortOrder === 'oldest'
  const history = useRequestHistory(ids, visible, remote)
  const refreshHistory = history.reload
  const cache = useContext(ResourceCache)
  const lastCertificateRevision = useRef<string | null>(null)
  const [certificateRevision, setCertificateRevision] = useState(0)
  const refreshCertificateRequests = useCallback((revision: string) => {
    if (revision === lastCertificateRevision.current) return
    lastCertificateRevision.current = revision
    invalidateCertificateCards(cache)
    refreshHistory()
  }, [cache, refreshHistory])
  useEffect(() => {
    if (ownCertificateRevision) refreshCertificateRequests(ownCertificateRevision)
  }, [ownCertificateRevision, refreshCertificateRequests])
  useEffect(() => {
    if (!watchQueue) return
    return watchCertificateUpdates((revision) => {
      if (revision === lastCertificateRevision.current) return
      refreshCertificateRequests(revision)
      setCertificateRevision(value => value + 1)
    }, refreshHistory)
  }, [watchQueue, refreshCertificateRequests, refreshHistory])
  useEffect(() => watchRequestIds(() => {
    if (remote) refreshHistory()
    else setIds((current) => [...new Set([...readRequestIds(), ...current])])
  }), [remote, refreshHistory])
  const matches = useMemo(() => matchRequests(history.data, activeFilter, query, oldestFirst),
    [history.data, activeFilter, query, oldestFirst])
  const failed = history.data?.some((result) => result.status === 'rejected')

  function changeSearch(value: string) {
    setQuery(value)
    saveRequestPreference('search', value)
  }

  function resetView() {
    changeSearch('')
    saveRequestPreference('view', '')
    const { parameters: params } = readHash(window.location.hash)
    params.delete('status')
    params.delete('sort')
    window.location.hash = requestsHref(params)
  }

  function created(record: RequestRecord) {
    const next = [record.id, ...ids.filter((id) => id !== record.id)]
    setIds(next)
    if (remote) refreshHistory()
    else setStorageError(!saveRequestIds(next))
  }

  return {
    history, matches, failed, query, activeFilter, oldestFirst, storageError,
    count: remote ? history.data?.length : ids.length,
    showLocalHistoryHint: !remote && !ids.length,
    certificateRevision, changeSearch, resetView, created,
  }
}
