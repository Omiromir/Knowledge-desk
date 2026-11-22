import { useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { watchCertificateUpdates } from '../../api/certificates'
import { getRequest, listRequests } from '../../api/requests'
import { isObject, type DocumentRecord, type RequestRecord, type RequestStatus } from '../../api/types'
import { AsyncState } from '../../components/AsyncState'
import { Icon } from '../../components/Icons'
import { LoadingMark } from '../../components/LoadingMark'
import { useResource } from '../../hooks/useResource'
import { REQUEST_STALE_AFTER_MS, ResourceCache } from '../../hooks/resourceCache'
import { readRequestIds, readRequestPreference, saveRequestIds, saveRequestPreference, watchRequestIds } from './history'
import { CertificateRequestForm } from './EmploymentCertificate'
import { RequestDetail, RequestHistoryRow } from './RequestDetail'
import { RequestForm } from './RequestForm'
import { useReplyDraft } from './replyDrafts'
import type { Member } from '../auth/AuthGate'

const statusOptions = {
  all: 'Все статусы',
  new: 'Новые',
  in_progress: 'В работе',
  awaiting_employee: 'Ждут ответа',
  resolved: 'Закрытые',
} satisfies Record<'all' | RequestStatus | 'awaiting_employee', string>

function setViewOption(name: 'status' | 'sort', value: string | null) {
  const params = new URLSearchParams(window.location.hash.split('?', 2)[1])
  if (value) params.set(name, value)
  else params.delete(name)
  updateRequestHash(params)
}

function updateRequestHash(params: URLSearchParams) {
  window.location.hash = params.size ? `#requests?${params}` : '#requests'
}

const inboxMedia = window.matchMedia('(min-width: 1000px)')
function watchInboxWidth(notify: () => void) {
  inboxMedia.addEventListener('change', notify)
  return () => inboxMedia.removeEventListener('change', notify)
}

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

export function RequestWorkspace({
  visible,
  member,
  ownCertificateRevision,
  requestId,
  composeRequested,
  certificateRequested,
  statusFilter,
  sortOrder,
  documentId,
  documents,
  documentsLoading,
  suggestedQuestion,
  onUseSuggestedQuestion,
}: {
  visible: boolean
  member?: Member
  ownCertificateRevision?: string
  requestId: string | null
  composeRequested: boolean
  certificateRequested: boolean
  statusFilter: string | null
  sortOrder: string | null
  documentId: string | null
  documents: DocumentRecord[]
  documentsLoading: boolean
  suggestedQuestion: string
  onUseSuggestedQuestion: () => void
}) {
  const [ids, setIds] = useState(readRequestIds)
  const wide = useSyncExternalStore(watchInboxWidth, () => inboxMedia.matches)
  const role = member?.role
  const operator = Boolean(member?.can_operate_requests)
  const returnRow = useRef<{ link: HTMLAnchorElement; top: number } | null>(null)
  useLayoutEffect(() => {
    if (!visible || requestId || composeRequested || !returnRow.current) return
    const { link, top } = returnRow.current
    returnRow.current = null
    if (link.isConnected) {
      window.scrollTo(0, top)
      link.focus({ preventScroll: true })
    }
  }, [visible, requestId, composeRequested])
  const [query, setQuery] = useState(() => readRequestPreference('search'))
  const [storageError, setStorageError] = useState(false)
  const filter = Object.keys(statusOptions).find((value) => value === statusFilter) as keyof typeof statusOptions | undefined
  const activeFilter = filter ?? 'all'
  const oldestFirst = sortOrder === 'oldest'
  const historyVisible = visible && (operator || (!requestId && !composeRequested))
  const history = useRequestHistory(ids, historyVisible, Boolean(role))
  const refreshHistory = history.reload
  const cache = useContext(ResourceCache)
  const lastCertificateRevision = useRef<string | null>(null)
  const [certificateRevision, setCertificateRevision] = useState(0)
  const watchQueue = visible && operator && Boolean(member?.can_issue_certificate)
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
    if (role) refreshHistory()
    else setIds((current) => [...new Set([...readRequestIds(), ...current])])
  }), [role, refreshHistory])
  const matches = useMemo(() => matchRequests(history.data, activeFilter, query, oldestFirst),
    [history.data, activeFilter, query, oldestFirst])
  const failed = history.data?.some((result) => result.status === 'rejected')
  const replyDraft = useReplyDraft(operator ? member?.user_id : undefined, requestId)
  const viewOptions = new URLSearchParams()
  if (activeFilter !== 'all') viewOptions.set('status', activeFilter)
  if (oldestFirst) viewOptions.set('sort', 'oldest')
  const viewQuery = viewOptions.toString()
  const requestListHref = viewQuery ? `#requests?${viewQuery}` : '#requests'
  const requestComposeHref = viewQuery ? `#requests?compose&${viewQuery}` : '#requests?compose'

  function changeSearch(value: string) {
    setQuery(value)
    saveRequestPreference('search', value)
  }

  function resetView() {
    changeSearch('')
    saveRequestPreference('view', '')
    const params = new URLSearchParams(window.location.hash.split('?', 2)[1])
    params.delete('status')
    params.delete('sort')
    updateRequestHash(params)
  }

  function created(record: RequestRecord) {
    const next = [record.id, ...ids.filter((id) => id !== record.id)]
    setIds(next)
    if (role) refreshHistory()
    else setStorageError(!saveRequestIds(next))
  }

  function renderHeading() {
    const loading = history.loading || history.refreshing
    const updating = history.refreshing && !history.error && !failed
    return (
      <div className="request-history-heading">
        <label className="search-field request-search-field">
          <Icon name="search" />
          <input
            className="request-search"
            name="request-search"
            type="search"
            aria-label="Поиск по вопросам и ответам"
            placeholder="Поиск по заявкам…"
            autoComplete="off"
            value={query}
            onChange={(event) => changeSearch(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Escape') changeSearch('')
            }}
          />
        </label>
        <div className="request-history-controls">
          <div className="request-view-options">
            <label className="request-view-control">
              <Icon name="filter" />
              <select
                name="request-status"
                aria-label="Фильтр заявок по статусу"
                value={activeFilter}
                onChange={(event) => setViewOption('status', event.target.value === 'all' ? null : event.target.value)}
              >
                {Object.entries(statusOptions).map(([value, label]) =>
                  <option key={value} value={value}>{!operator && value === 'awaiting_employee' ? 'Нужен ваш ответ' : label}</option>,
                )}
              </select>
            </label>
            <select
              name="request-sort"
              aria-label="Сортировка заявок по дате отправки"
              value={oldestFirst ? 'oldest' : 'newest'}
              onChange={(event) => setViewOption('sort', event.target.value === 'oldest' ? 'oldest' : null)}
            >
              <option value="newest">Сначала новые</option>
              <option value="oldest">Сначала старые</option>
            </select>
            <button
              className="request-view-reset"
              type="button"
              disabled={!query && !statusFilter && !sortOrder}
              onClick={resetView}
            >
              Сбросить
            </button>
          </div>
          <button type="button" className="icon-button outline-button" aria-label="Обновить заявки" title="Обновить заявки"
            aria-disabled={loading} onClick={() => { if (!loading) refreshHistory() }}>
            {updating ? <LoadingMark compact /> : <Icon name="reload" />}
          </button>
          <span className="refresh-status" role="status">{updating ? 'Обновляем заявки…' : ''}</span>
        </div>
      </div>
    )
  }

  return (
    <section className="requests-page" aria-label="Отправка и просмотр заявок">
      <header className="location-bar">
        {requestId || composeRequested ? <a className="text-button" href={requestListHref}>
          <Icon name="back" /> К списку заявок
        </a> : <><Icon name="inbox" /><h1>Заявки</h1></>}
        {((!requestId && !composeRequested) || (operator && wide)) && (
          <span className="request-history-count" role="status">
            {history.data !== null && `${matches.length} из ${role ? history.data.length : ids.length}`}
          </span>
        )}
        {!composeRequested && <div className="request-header-actions">
          {!requestId && member?.can_request_certificate && (
            <a className="button secondary certificate-compose" href={`${requestComposeHref}&certificate`}>Запросить справку</a>
          )}
          <a className="button secondary inbox-compose" href={requestComposeHref}>
            <Icon name="compose" />Новая заявка
          </a>
        </div>}
      </header>
      <div className={operator ? 'operator-inbox' : 'request-workspace'} data-open={Boolean(requestId || composeRequested)} data-compose={composeRequested || undefined}>
        <aside className="request-history" hidden={Boolean(requestId || composeRequested) && !operator}
          onClickCapture={(event) => {
            const link = event.target instanceof Element ? event.target.closest('a') : null
            if (link && !event.ctrlKey && !event.metaKey) returnRow.current = { link, top: window.scrollY }
          }}
          aria-label={operator ? 'Очередь заявок' : 'Мои заявки'}>
          {renderHeading()}
          {operator && <h2 className="request-history-label">Очередь заявок</h2>}
          {history.loading && !history.error && <AsyncState title="Загружаем заявки…" loading />}
          {matches.length > 0 && <ul className="history-list load-result">
            {matches.map(({ record, questionMatch, replyMatch }) => <li key={record.id}>
              <RequestHistoryRow operator={operator} selected={requestId === record.id} record={record}
                today={history.today} questionMatch={questionMatch} replyMatch={replyMatch}
                href={`#requests?id=${encodeURIComponent(record.id)}${viewQuery ? `&${viewQuery}` : ''}`} />
            </li>)}
          </ul>}
          {history.error && <AsyncState title="Список заявок недоступен" description={history.error} error inline={matches.length > 0} onRetry={history.reload} retrying={history.loading || history.refreshing} />}
          {failed && !history.error && <AsyncState title="Не удалось прочитать часть заявок" error inline={matches.length > 0} onRetry={history.reload} retrying={history.loading || history.refreshing} />}
          {!matches.length && !history.loading && !history.error && !failed && <AsyncState
            title={query.trim() ? 'По запросу ничего не найдено' : activeFilter === 'all' ? 'Заявок пока нет' : 'Заявок с таким статусом нет'}
            description={!role && !ids.length ? 'Здесь появятся заявки, отправленные из этого браузера.' : undefined} />}
          {storageError && <p className="field-error" role="status">История не сохранится после закрытия страницы.</p>}
        </aside>
        <div className="request-form-pane" hidden={Boolean(requestId) || !composeRequested}>
          {certificateRequested ? <CertificateRequestForm onCreated={(record) => {
            created(record)
            const params = new URLSearchParams(viewQuery)
            params.set('id', record.id)
            updateRequestHash(params)
          }} /> : <RequestForm
            onCreated={created}
            requestListHref={requestListHref}
            requestComposeHref={requestComposeHref}
            documentId={documentId}
            documentTitle={documents.find((document) => document.id === documentId)?.title}
            documentsLoading={documentsLoading}
            suggestedQuestion={suggestedQuestion}
            onUseSuggestedQuestion={onUseSuggestedQuestion}
          />}
        </div>
        {requestId ? <div className="thread-pane" key={requestId} role="region" aria-label="Открытая заявка" tabIndex={-1}>
          <RequestDetail id={requestId} documents={documents} canOperate={!role || operator} conversation={operator}
            canIssueCertificate={Boolean(member?.can_issue_certificate)}
            certificateRevision={certificateRevision}
            draft={replyDraft.draft} draftStorageError={replyDraft.storageError}
            onDraft={replyDraft.changeDraft} onChanged={history.refresh} />
        </div> : null}
      </div>
    </section>
  )
}
