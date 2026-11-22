import type { MouseEventHandler } from 'react'
import type { RequestRecord } from '../../api/types'
import { AsyncState } from '../../components/AsyncState'
import { Icon } from '../../components/Icons'
import { LoadingMark } from '../../components/LoadingMark'
import { readHash, requestsHref } from '../../navigation/routes'
import { requestDateTime } from './requestPresentation'
import { RequestStatusLabel } from './RequestStatusLabel'
import { statusOptions, type useRequestQueue } from './useRequestQueue'

function setViewOption(name: 'status' | 'sort', value: string | null) {
  const { parameters: params } = readHash(window.location.hash)
  if (value) params.set(name, value)
  else params.delete(name)
  window.location.hash = requestsHref(params)
}

const requestDate = new Intl.DateTimeFormat('ru-RU', {
  day: 'numeric', month: 'long', year: 'numeric',
})
const requestTime = new Intl.DateTimeFormat('ru-RU', {
  hour: '2-digit', minute: '2-digit',
})

function highlightedMatch(match: RegExpExecArray, start = 0, end = match.input.length) {
  const after = match.index + match[0].length
  return (
    <>
      {start > 0 && '…'}{match.input.slice(start, match.index)}
      <mark>{match[0]}</mark>
      {match.input.slice(after, end)}{end < match.input.length && '…'}
    </>
  )
}

function RequestHistoryRow({ record, href, today, questionMatch, replyMatch, selected, operator }: {
  record: RequestRecord
  href: string
  today: string
  selected: boolean
  operator: boolean
  questionMatch?: RegExpExecArray | null
  replyMatch?: RegExpExecArray | null
}) {
  const date = record.created_at ? new Date(record.created_at) : null
  const isToday = date?.toDateString() === today
  return (
    <a className="request-history-row" href={href} aria-current={selected ? 'true' : undefined}>
      {operator && <span className="queue-author"><span aria-hidden="true">{record.name.trim().slice(0, 1).toUpperCase()}</span>{record.name}</span>}
      <span className="request-history-copy">
        <span className="request-question">
          {questionMatch ? highlightedMatch(questionMatch) : record.question}
        </span>
        {replyMatch && (
          <span className="request-match-snippet">
            {highlightedMatch(replyMatch, Math.max(0, replyMatch.index - 20), replyMatch.index + replyMatch[0].length + 75)}
          </span>
        )}
      </span>
      <span className="request-history-meta">
        <RequestStatusLabel record={record} personal={!operator} />
        {date ? (
          <time className="request-history-date" dateTime={record.created_at} title={requestDateTime.format(date)}>
            {(isToday ? requestTime : requestDate).format(date)}
          </time>
        ) : (
          <span className="request-history-date" title="Дата отправки неизвестна">—</span>
        )}
      </span>
    </a>
  )
}

export function RequestQueue({ queue, requestId, operator, hidden, viewQuery, hasViewOptions, onClickCapture }: {
  queue: ReturnType<typeof useRequestQueue>
  requestId: string | null
  operator: boolean
  hidden: boolean
  viewQuery: string
  hasViewOptions: boolean
  onClickCapture: MouseEventHandler<HTMLElement>
}) {
  const {
    history, matches, failed, query, activeFilter, oldestFirst, storageError, showLocalHistoryHint,
    changeSearch, resetView,
  } = queue

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
              disabled={!query && !hasViewOptions}
              onClick={resetView}
            >
              Сбросить
            </button>
          </div>
          <button type="button" className="icon-button outline-button" aria-label="Обновить заявки" title="Обновить заявки"
            aria-disabled={loading} onClick={() => { if (!loading) history.reload() }}>
            {updating ? <LoadingMark compact /> : <Icon name="reload" />}
          </button>
          <span className="refresh-status" role="status">{updating ? 'Обновляем заявки…' : ''}</span>
        </div>
      </div>
    )
  }

  return (
    <aside className="request-history" hidden={hidden} onClickCapture={onClickCapture}
      aria-label={operator ? 'Очередь заявок' : 'Мои заявки'}>
      {renderHeading()}
      {operator && <h2 className="request-history-label">Очередь заявок</h2>}
      {history.loading && !history.error && <AsyncState title="Загружаем заявки…" loading />}
      {matches.length > 0 && <ul className="history-list load-result">
        {matches.map(({ record, questionMatch, replyMatch }) => <li key={record.id}>
          <RequestHistoryRow operator={operator} selected={requestId === record.id} record={record}
            today={history.today} questionMatch={questionMatch} replyMatch={replyMatch}
            href={requestsHref(viewQuery, { requestId: record.id })} />
        </li>)}
      </ul>}
      {history.error && <AsyncState title="Список заявок недоступен" description={history.error} error inline={matches.length > 0} onRetry={history.reload} retrying={history.loading || history.refreshing} />}
      {failed && !history.error && <AsyncState title="Не удалось прочитать часть заявок" error inline={matches.length > 0} onRetry={history.reload} retrying={history.loading || history.refreshing} />}
      {!matches.length && !history.loading && !history.error && !failed && <AsyncState
        title={query.trim() ? 'По запросу ничего не найдено' : activeFilter === 'all' ? 'Заявок пока нет' : 'Заявок с таким статусом нет'}
        description={showLocalHistoryHint ? 'Здесь появятся заявки, отправленные из этого браузера.' : undefined} />}
      {storageError && <p className="field-error" role="status">История не сохранится после закрытия страницы.</p>}
    </aside>
  )
}
