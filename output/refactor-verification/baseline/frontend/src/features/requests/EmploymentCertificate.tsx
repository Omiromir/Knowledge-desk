import { useRef, useState, type FormEvent } from 'react'
import {
  certificateUrl, issueCertificate, MAX_CERTIFICATE_PDF_BYTES, MAX_CLARIFICATION_LENGTH, requestCertificate,
  sendCertificateClarification,
} from '../../api/certificates'
import { errorMessage } from '../../api/client'
import type { CertificateEmailStatus, MessageAuthor, RequestRecord } from '../../api/types'
import { Icon } from '../../components/Icons'
import { LoadingMark } from '../../components/LoadingMark'
import { ProfileAvatar } from '../profile/ProfileAvatar'
import { useMessageAuthor } from './useMessageAuthor'

const emailLabels: Record<CertificateEmailStatus, string> = {
  pending: 'Ожидает отправки',
  accepted: 'Ожидает подтверждения доставки',
  delayed: 'Доставка задерживается',
  delivered: 'Доставлено',
  not_delivered: 'Не доставлено',
  unknown: 'Статус неизвестен',
}

const clarificationDate = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeStyle: 'short' })

function ClarificationMessage({ author, fallback, requestId, date, text }: {
  author?: MessageAuthor; fallback: string; requestId: string; date: string; text: string
}) {
  const identity = useMessageAuthor(author, fallback, requestId)
  return (
    <div className="certificate-clarification-entry">
      <ProfileAvatar className="request-avatar" {...identity} size={24} />
      <div>
        <p><strong>{identity.name}</strong> <time className="thread-time" dateTime={date}>
          {clarificationDate.format(new Date(date))}
        </time></p>
        <p className="certificate-message">{text}</p>
      </div>
    </div>
  )
}

export function CertificateRequestForm({ onCreated }: { onCreated: (record: RequestRecord) => void }) {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const sending = useRef(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (sending.current) return
    const purpose = String(new FormData(event.currentTarget).get('purpose') ?? '').trim()
    sending.current = true
    setPending(true)
    setError('')
    try {
      onCreated(await requestCertificate(purpose))
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      sending.current = false
      setPending(false)
    }
  }

  return (
    <form className="request-form certificate-request-form" onSubmit={submit}>
      <h1>Справка с места работы</h1>
      <div className="field">
        <label htmlFor="certificate-purpose">Назначение справки</label>
        <input id="certificate-purpose" name="purpose" aria-describedby="certificate-purpose-hint"
          maxLength={160} autoComplete="off" readOnly={pending} />
        <p className="muted" id="certificate-purpose-hint">Оставьте пустым для справки по месту требования.</p>
      </div>
      {error && <p className="field-error" role="alert">{error}</p>}
      <button type="submit" className="button primary" disabled={pending}>
        {pending && <LoadingMark compact />}
        {pending ? 'Отправляем…' : 'Отправить запрос'}
      </button>
    </form>
  )
}

export function CertificatePanel({ record, canIssue, onChanged, refreshing, refreshError, onRefresh }: {
  record: RequestRecord
  canIssue: boolean
  onChanged: (record: RequestRecord) => void
  refreshing: boolean
  refreshError: string
  onRefresh: () => void
}) {
  const [attachment, setAttachment] = useState<{ file: File; rounds: number } | null>(null)
  const [error, setError] = useState('')
  const [pending, setPending] = useState<'issue' | 'clarification' | null>(null)
  const [asking, setAsking] = useState(false)
  const [draft, setDraft] = useState({ target: 'question', text: '' })
  const [messageError, setMessageError] = useState('')
  const sending = useRef(false)
  const attempt = useRef<{ id: string; text: string } | null>(null)
  const ready = record.status === 'resolved'
  const waiting = Boolean(record.certificate_awaiting_reply)
  const canAsk = !ready && canIssue && !waiting
  const history = record.certificate_clarifications ?? []
  const toAnswer = record.certificate_can_answer ? history.find(entry => !entry.answered_at) : undefined
  const target = toAnswer?.id ?? 'question'
  const message = draft.target === target ? draft.text : ''
  const file = attachment?.rounds === history.length ? attachment.file : null
  const input = useRef<HTMLInputElement>(null)
  const result = useRef<HTMLParagraphElement>(null)
  const clarificationInput = useRef<HTMLTextAreaElement>(null)
  const askButton = useRef<HTMLButtonElement>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (sending.current || !file || !canAsk || asking) return
    if (file.size === 0 || file.size > MAX_CERTIFICATE_PDF_BYTES || !file.name.toLowerCase().endsWith('.pdf')) {
      setError('Выберите подписанный PDF размером до 5 МиБ.')
      input.current?.focus()
      return
    }
    sending.current = true
    setPending('issue')
    setError('')
    try {
      onChanged(await issueCertificate(record.id, file))
      result.current?.focus({ preventScroll: true })
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      sending.current = false
      setPending(null)
    }
  }

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const text = message.trim()
    if (sending.current || !text || (!toAnswer && !(canAsk && asking))) return
    const previous = attempt.current
    const id = toAnswer?.id ?? (previous?.text === text ? previous.id : crypto.randomUUID())
    attempt.current = { id, text }
    sending.current = true
    setPending('clarification')
    setMessageError('')
    try {
      onChanged(await sendCertificateClarification(record.id, id, text, Boolean(toAnswer)))
      setDraft({ target, text: '' })
      setAsking(false)
      attempt.current = null
      result.current?.focus({ preventScroll: true })
    } catch (failure) {
      setMessageError(errorMessage(failure))
    } finally {
      sending.current = false
      setPending(null)
    }
  }

  return (
    <section className="certificate-panel" aria-labelledby="certificate-title">
      <div className="certificate-heading">
        <h2 id="certificate-title">Справка</h2>
        {!ready && record.certificate_downloadable && (
          <button type="button" className="icon-button outline-button"
            aria-label="Обновить справку" title="Обновить справку" aria-disabled={refreshing || Boolean(pending)}
            onClick={() => { if (!refreshing && !pending) onRefresh() }}>
            {refreshing ? <LoadingMark compact /> : <Icon name="reload" />}
          </button>
        )}
      </div>
      <p ref={result} role="status" tabIndex={-1} className="muted">
        {ready ? 'Справка готова' : toAnswer ? 'Нужен ваш ответ' : waiting ? 'Ждёт ответа сотрудника' : 'Ожидает проверки кадровиком'}
      </p>
      {ready && record.certificate_downloadable && (
        <a className="button secondary" href={certificateUrl(record.id, 'issued')}>Скачать справку (PDF)</a>
      )}
      {ready && record.certificate_email_status && (
        <>
          <div className="certificate-mail">
            <p role="status" aria-busy={refreshing} className="muted">
              Письмо <span data-status={record.certificate_email_status}>{emailLabels[record.certificate_email_status]}</span>
            </p>
            <button type="button" className="icon-button outline-button"
              aria-label="Обновить статус письма" title="Обновить статус письма" aria-disabled={refreshing}
              onClick={() => { if (!refreshing) onRefresh() }}>
              {refreshing ? <LoadingMark compact /> : <Icon name="reload" />}
            </button>
          </div>
          {refreshError && <p className="field-error" role="status">Не удалось обновить статус письма.</p>}
        </>
      )}
      {history.length > 0 && (
        <ol className="certificate-clarifications" aria-label="История уточнений">
          {history.map(entry => (
            <li key={entry.id}>
              <ClarificationMessage author={entry.question_author} fallback="Кадровик" requestId={record.id}
                date={entry.asked_at} text={entry.question} />
              {entry.answer && entry.answered_at && (
                <ClarificationMessage author={entry.answer_author} fallback="Сотрудник" requestId={record.id}
                  date={entry.answered_at} text={entry.answer} />
              )}
            </li>
          ))}
        </ol>
      )}
      {canAsk && !asking && (
        <div className="certificate-clarification-actions">
          <button type="button" className="button secondary" ref={askButton} disabled={Boolean(pending)}
            onClick={() => {
              setAttachment(null)
              setError('')
              setAsking(true)
              window.requestAnimationFrame(() => clarificationInput.current?.focus())
            }}>
            Запросить уточнение
          </button>
          <a className="button secondary" href={certificateUrl(record.id, 'draft')}>Скачать черновик (Word)</a>
        </div>
      )}
      {(toAnswer || (canAsk && asking)) && (
        <form className="certificate-clarification-form" onSubmit={sendMessage}>
          <div className="field">
            <label htmlFor="certificate-clarification">{toAnswer ? 'Ваш ответ' : 'Что нужно уточнить?'}</label>
            <textarea id="certificate-clarification" name="clarification" ref={clarificationInput} rows={3}
              maxLength={MAX_CLARIFICATION_LENGTH} value={message} readOnly={Boolean(pending)} required
              aria-describedby={messageError ? 'certificate-clarification-error' : undefined}
              onChange={(event) => { setDraft({ target, text: event.target.value }); setMessageError('') }} />
          </div>
          {messageError && <p id="certificate-clarification-error" className="field-error" role="alert">{messageError}</p>}
          <div className="certificate-clarification-actions">
            <button type="submit" className="button primary" disabled={!message.trim() || Boolean(pending)}>
              {pending === 'clarification' && <LoadingMark compact />}
              {pending === 'clarification' ? 'Отправляем…' : toAnswer ? 'Отправить ответ' : 'Отправить уточнение'}
            </button>
            {!toAnswer && <button type="button" className="button secondary" disabled={Boolean(pending)}
              onClick={() => {
                setAsking(false)
                setDraft({ target, text: '' })
                setMessageError('')
                window.requestAnimationFrame(() => askButton.current?.focus())
              }}>Отмена</button>}
          </div>
        </form>
      )}
      {canAsk && !asking && (
        <form className="certificate-issue-form" onSubmit={submit}>
          <div className="field">
            <label htmlFor="certificate-signed-file">Подписанная справка (PDF)</label>
            <input id="certificate-signed-file" ref={input} type="file" accept=".pdf,application/pdf"
              aria-describedby={error ? 'certificate-pdf-hint certificate-pdf-error' : 'certificate-pdf-hint'}
              disabled={Boolean(pending)} onChange={(event) => {
                const selected = event.target.files?.[0]
                setAttachment(selected ? { file: selected, rounds: history.length } : null)
                setError('')
              }} />
            <p className="muted" id="certificate-pdf-hint">До 5 МиБ. После выдачи файл станет доступен заявителю.</p>
          </div>
          {error && <p id="certificate-pdf-error" role="alert" className="field-error">{error}</p>}
          <button type="submit" className="button primary" disabled={!file || Boolean(pending)}>
            {pending === 'issue' && <LoadingMark compact />}
            {pending === 'issue' ? 'Сохраняем…' : 'Выдать справку'}
          </button>
        </form>
      )}
    </section>
  )
}
