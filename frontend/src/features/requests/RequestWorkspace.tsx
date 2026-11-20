import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { getRequest, listRequests } from '../../api/requests'
import type { DocumentRecord, RequestRecord, RequestStatus } from '../../api/types'
import { AsyncState } from '../../components/AsyncState'
import { Icon } from '../../components/Icons'
import { useResource } from '../../hooks/useResource'
import { readRequestIds, readRequestPreference, saveRequestIds, saveRequestPreference, watchRequestIds } from './history'
import { RequestDetail, RequestHistoryRow } from './RequestDetail'
import { RequestForm } from './RequestForm'
import type { Member } from '../auth/AuthGate'

const statusOptions = {
  all: 'Все статусы',
  new: 'Новые',
  in_progress: 'В работе',
  resolved: 'Закрытые',
} satisfies Record<'all' | RequestStatus, string>

function setViewOption(name: 'status' | 'sort', value: string | null) {
  const params = new URLSearchParams(window.location.hash.split('?', 2)[1])
  if (value) params.set(name, value)
  else params.delete(name)
  updateRequestHash(params)
}

function updateRequestHash(params: URLSearchParams) {
  window.location.hash = params.size ? `#requests?${params}` : '#requests'
}

function RequestHistory({ ids, remote, filter, query, oldestFirst, viewQuery, renderHeading }: {
  ids: string[]
  remote: boolean
  filter: keyof typeof statusOptions
  query: string
  oldestFirst: boolean
  viewQuery: string
  renderHeading: (count: number | null, total: number) => ReactNode
}) {
  const load = useCallback(
    async (signal: AbortSignal): Promise<PromiseSettledResult<RequestRecord>[]> => remote
      ? (await listRequests(signal)).map((value) => ({ status: 'fulfilled', value }))
      : Promise.allSettled(ids.map((id) => getRequest(id, signal))),
    [ids, remote],
  )
  const { data, error, loading, reload } = useResource(load)
  const [today, setToday] = useState(() => new Date().toDateString())
  useEffect(() => {
    const midnight = new Date()
    midnight.setHours(24, 0, 0, 0)
    const timer = window.setTimeout(() => setToday(new Date().toDateString()), midnight.getTime() - Date.now())
    return () => window.clearTimeout(timer)
  }, [today])
  if (!remote && !ids.length) return (
    <>
      {renderHeading(0, 0)}
      <AsyncState title="Заявок пока нет" description="Здесь появятся заявки, отправленные из этого браузера." />
    </>
  )
  if (loading) return <>{renderHeading(null, 0)}<AsyncState title="Загружаем заявки…" loading /></>
  if (error) return <>{renderHeading(null, 0)}<AsyncState title="Список заявок недоступен" description={error} error onRetry={reload} /></>
  const term = query.trim()
  const pattern = term ? new RegExp(term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'iu') : null
  const matches = data?.flatMap((result) => {
    if (result.status !== 'fulfilled') return []
    const record = result.value
    if (filter !== 'all' && record.status !== filter) return []
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
  const failed = data?.some((result) => result.status === 'rejected')
  return (
    <>
      {renderHeading(matches.length, remote ? data?.length ?? 0 : ids.length)}
      {failed && <AsyncState title="Не удалось прочитать часть заявок" error onRetry={reload} />}
      {matches.length ? (
        <ul className="history-list">
          {matches.map(({ record, questionMatch, replyMatch }) => (
            <li key={record.id}>
              <RequestHistoryRow
                record={record}
                today={today}
                questionMatch={questionMatch}
                replyMatch={replyMatch}
                href={`#requests?id=${encodeURIComponent(record.id)}${viewQuery ? `&${viewQuery}` : ''}`}
              />
            </li>
          ))}
        </ul>
      ) : !failed && <AsyncState title={term ? 'По запросу ничего не найдено' : filter === 'all' ? 'Заявок пока нет' : 'Заявок с таким статусом нет'} />}
    </>
  )
}

export function RequestWorkspace({
  visible,
  role,
  requestId,
  statusFilter,
  sortOrder,
  documentId,
  documents,
  documentsLoading,
  suggestedQuestion,
  onUseSuggestedQuestion,
}: {
  visible: boolean
  role?: Member['role']
  requestId: string | null
  statusFilter: string | null
  sortOrder: string | null
  documentId: string | null
  documents: DocumentRecord[]
  documentsLoading: boolean
  suggestedQuestion: string
  onUseSuggestedQuestion: () => void
}) {
  const [ids, setIds] = useState(readRequestIds)
  useEffect(() => watchRequestIds(() => setIds((current) => [...new Set([...readRequestIds(), ...current])])), [])
  const [query, setQuery] = useState(() => readRequestPreference('search'))
  const [storageError, setStorageError] = useState(false)
  const filter = Object.keys(statusOptions).find((value) => value === statusFilter) as keyof typeof statusOptions | undefined
  const activeFilter = filter ?? 'all'
  const oldestFirst = sortOrder === 'oldest'
  const viewOptions = new URLSearchParams()
  if (activeFilter !== 'all') viewOptions.set('status', activeFilter)
  if (oldestFirst) viewOptions.set('sort', 'oldest')
  const viewQuery = viewOptions.toString()
  const requestListHref = viewQuery ? `#requests?${viewQuery}` : '#requests'

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
    if (!role) setStorageError(!saveRequestIds(next))
  }

  function renderHeading(count: number | null, total: number) {
    return (
      <div className="request-history-heading">
        <div className="request-history-title">
          <h2>{role === 'operator' ? 'Очередь заявок' : 'Отправленные'}</h2>
          <span className="request-history-count" role="status">
            {count !== null && `${count} из ${total}`}
          </span>
        </div>
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
        <div className="request-view-options">
          <label className="request-view-control">
            <Icon name="filter" />
            <select
              name="request-status"
              aria-label="Фильтр заявок по статусу"
              value={activeFilter}
              onChange={(event) => setViewOption('status', event.target.value === 'all' ? null : event.target.value)}
            >
              {Object.entries(statusOptions).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
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
      </div>    )
  }

  return (
    <section className="requests-page" aria-label="Отправка и просмотр заявок">
      <header className="location-bar">
        {requestId ? (
          <a className="text-button" href={requestListHref}>
            <Icon name="back" /> Заявки
          </a>
        ) : (
          <>
            <Icon name="inbox" />
            <h1>Заявки</h1>
          </>
        )}
      </header>
      {requestId && <RequestDetail key={requestId} id={requestId} documents={documents} canOperate={!role || role === 'operator'} />}
      <div className="request-workspace" hidden={Boolean(requestId)}>
        <RequestForm
          onCreated={created}
          requestListHref={requestListHref}
          documentId={documentId}
          documentTitle={
            documents.find((document) => document.id === documentId)?.title
          }
          documentsLoading={documentsLoading}
          suggestedQuestion={suggestedQuestion}
          onUseSuggestedQuestion={onUseSuggestedQuestion}
        />
        <aside
          className="request-history"
          aria-label={role === 'operator' ? 'Очередь заявок' : 'Мои заявки'}
        >
          {visible && !requestId && <RequestHistory
            ids={ids}
            remote={Boolean(role)}
            filter={activeFilter}
            query={query}
            oldestFirst={oldestFirst}
            viewQuery={viewQuery}
            renderHeading={renderHeading}
          />}
          {storageError ? (
            <p className="field-error" role="status">
              История не сохранится после закрытия страницы.
            </p>
          ) : null}
        </aside>
      </div>
    </section>
  )
}
