import { useCallback, useRef, useState, type FormEvent } from 'react'
import { ApiError, errorMessage } from '../../api/client'
import { getDelivery } from '../../api/delivery'
import { changeRequestReply, getRequest, MAX_REQUEST_REPLY_LENGTH, replyToRequest, updateRequestStatus } from '../../api/requests'
import type { DocumentRecord, RequestRecord, RequestStatus } from '../../api/types'
import { AsyncState } from '../../components/AsyncState'
import { DeliveryStatus } from '../../components/DeliveryStatus'
import { useResource } from '../../hooks/useResource'
import { RequestReplyItem } from './RequestReplyItem'

const workflow: Record<RequestStatus, { label: string; action: string; next: RequestStatus }> = {
  new: { label: 'Новая', action: 'Взять в работу', next: 'in_progress' },
  in_progress: { label: 'В работе', action: 'Закрыть заявку', next: 'resolved' },
  resolved: { label: 'Закрыта', action: 'Возобновить', next: 'in_progress' },
}
const requestDate = new Intl.DateTimeFormat('ru-RU', {
  day: 'numeric', month: 'long', year: 'numeric',
})
const requestTime = new Intl.DateTimeFormat('ru-RU', {
  hour: '2-digit', minute: '2-digit',
})
const requestDateTime = new Intl.DateTimeFormat('ru-RU', {
  day: 'numeric', month: 'long', year: 'numeric',
  hour: '2-digit', minute: '2-digit', second: '2-digit',
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

function useRequest(id: string) {
  const load = useCallback((signal: AbortSignal) => getRequest(id, signal), [id])
  return useResource(load)
}

function RequestStatusLabel({ status }: { status: RequestStatus }) {
  return <span className="request-status" data-status={status}>{workflow[status].label}</span>
}

export function RequestHistoryRow({ record, href, today, questionMatch, replyMatch }: {
  record: RequestRecord
  href: string
  today: string
  questionMatch?: RegExpExecArray | null
  replyMatch?: RegExpExecArray | null
}) {
  const date = record.created_at ? new Date(record.created_at) : null
  const isToday = date?.toDateString() === today
  return (
    <a className="request-history-row" href={href}>
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
        <RequestStatusLabel status={record.status} />
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

export function RequestDetail({
  id,
  documents,
  canOperate,
}: {
  id: string
  documents: DocumentRecord[]
  canOperate: boolean
}) {
  const { data, loading, error, reload } = useRequest(id)
  const loadDelivery = useCallback((signal: AbortSignal) => getDelivery(id, signal), [id])
  const delivery = useResource(loadDelivery)
  const [saved, setSaved] = useState<RequestRecord | null>(null)
  const [pending, setPending] = useState<'status' | 'reply' | 'manage' | null>(null)
  const [statusError, setStatusError] = useState('')
  const [replyError, setReplyError] = useState('')
  const [draft, setDraft] = useState('')
  const running = useRef(false)
  const attempt = useRef<{ id: string; text: string }>(null)
  const activityHeading = useRef<HTMLHeadingElement>(null)

  if (loading) return <AsyncState title="Загружаем заявку…" loading />
  if (error)
    return (
      <AsyncState
        title="Не удалось прочитать заявку"
        description={error}
        error
        onRetry={reload}
      />
    )
  const record = saved ?? data
  if (!record) return null
  const document = documents.find((entry) => entry.id === record.document_id)
  const status = workflow[record.status]
  const text = draft.trim()
  const canReply = record.status !== 'resolved'
  const tooLong = text.length > MAX_REQUEST_REPLY_LENGTH && [...text].length > MAX_REQUEST_REPLY_LENGTH

  async function manageReply(replyId: string, replacement: string | null) {
    if (running.current) throw new ApiError('Дождитесь завершения текущего действия.')
    running.current = true
    setPending('manage')
    try {
      setSaved(await changeRequestReply(id, replyId, replacement))
    } finally {
      running.current = false
      setPending(null)
    }
  }

  async function changeStatus() {
    if (running.current || !record) return
    running.current = true
    setPending('status')
    setStatusError('')
    try {
      setSaved(await updateRequestStatus(id, record.status, status.next))
      setReplyError('')
    } catch (failure) {
      if (failure instanceof ApiError && failure.code === 'request_status_conflict') {
        setSaved(null)
        reload()
        setStatusError('Статус уже изменился. Проверьте его и выберите действие снова.')
      } else {
        setStatusError(errorMessage(failure))
      }
    } finally {
      running.current = false
      setPending(null)
    }
  }

  async function sendReply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (running.current || !canReply || !text || tooLong) return
    running.current = true
    setPending('reply')
    setReplyError('')
    try {
      if (attempt.current?.text !== text) {
        attempt.current = { id: crypto.randomUUID(), text }
      }
      const updated = await replyToRequest(id, attempt.current.id, text)
      setSaved(updated)
      setDraft('')
      attempt.current = null
      activityHeading.current?.focus()
    } catch (failure) {
      if (failure instanceof ApiError && failure.code === 'request_reply_conflict') {
        setSaved(null)
        reload()
        setReplyError('Заявка уже закрыта. Возобновите её, чтобы отправить ответ.')
      } else {
        setReplyError(errorMessage(failure))
      }
    } finally {
      running.current = false
      setPending(null)
    }
  }

  return (
    <article className="request-issue" aria-labelledby="request-issue-title">
      <h1 id="request-issue-title">{record.question}</h1>
      <aside className="request-properties" aria-labelledby="request-properties-title">
        <h2 id="request-properties-title">Свойства</h2>
        <div className="request-workflow">
          <span role="status"><RequestStatusLabel status={record.status} /></span>
          {canOperate && <button className="text-button" type="button" aria-disabled={Boolean(pending)} onClick={changeStatus}>
            {pending === 'status' ? 'Сохраняем…' : status.action}
          </button>}
        </div>
        {statusError && <p className="field-error" role="alert">{statusError}</p>}
        <dl className="request-property-list">
          <div><dt>Автор</dt><dd>{record.name}</dd></div>
          {record.document_id && (
            <div>
              <dt>Документ</dt>
              <dd><a href={`#documents?id=${encodeURIComponent(record.document_id)}`}>
                {document?.title ?? 'Открыть документ'}
              </a></dd>
            </div>
          )}
        </dl>
        <details className="request-info">
          <summary>Детали заявки</summary>
          <div className="record-id">
            <span>ID заявки</span>
            <code translate="no">{record.id}</code>
          </div>
        </details>
        {delivery.data && <DeliveryStatus delivery={delivery.data} onRefresh={delivery.reload} allowRetry />}
        {delivery.error && <AsyncState title="Статус доставки недоступен" description={delivery.error} error onRetry={delivery.reload} />}
      </aside>
      <section className="request-activity" aria-labelledby="request-activity-title">
        <h2 id="request-activity-title" ref={activityHeading} tabIndex={-1}>Активность</h2>
        <ol className="request-activity-list">
          <li className="request-activity-event">
            <span className="request-avatar request-avatar-author" aria-hidden="true">{[...record.name][0]?.toUpperCase()}</span>
            <p><strong>{record.name}</strong> · создание заявки</p>
          </li>
          {record.replies?.map((reply) => (
            <RequestReplyItem key={reply.id} reply={reply} busy={Boolean(pending)}
              onChange={canOperate ? manageReply : undefined} onDeleted={() => activityHeading.current?.focus()} />
          ))}
        </ol>
        {canOperate && (canReply || draft) && (
          <div className="request-composer-row">
            <span className="request-avatar" aria-hidden="true">О</span>
            <form className="request-composer" onSubmit={sendReply}>
              <textarea
                name="reply"
                aria-label="Ответ на заявку"
                aria-describedby={replyError || tooLong ? 'request-reply-error' : undefined}
                aria-invalid={tooLong || undefined}
                autoComplete="off"
                placeholder="Написать ответ…"
                rows={3}
                value={draft}
                readOnly={pending === 'reply'}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.nativeEvent.isComposing) {
                    event.preventDefault()
                    event.currentTarget.form?.requestSubmit()
                  }
                }}
              />
              {(replyError || tooLong) && (
                <p id="request-reply-error" className="field-error" role="alert">
                  {tooLong ? 'Сократите ответ до 10 000 символов.' : replyError}
                </p>
              )}
              {!canReply && !replyError && <p className="field-error">Возобновите заявку, чтобы отправить ответ.</p>}
              <div className="request-composer-actions">
                <kbd aria-hidden="true">Ctrl ↵</kbd>
                <button className="button primary" type="submit" disabled={!canReply || !text || tooLong || Boolean(pending)}>
                  {pending === 'reply' && <span className="loading-mark" aria-hidden="true" />}
                  {pending === 'reply' ? 'Отправляем…' : 'Ответить и закрыть'}
                </button>
              </div>
            </form>
          </div>
        )}
      </section>
    </article>
  )
}
