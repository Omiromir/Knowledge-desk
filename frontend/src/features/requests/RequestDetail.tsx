import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { watchCertificateUpdates } from '../../api/certificates'
import { ApiError, errorMessage } from '../../api/client'
import { getDelivery } from '../../api/delivery'
import { changeRequestReply, getRequest, MAX_REQUEST_REPLY_LENGTH, replyToRequest, updateRequestStatus } from '../../api/requests'
import type { DocumentRecord, RequestRecord } from '../../api/types'
import { AsyncState } from '../../components/AsyncState'
import { DeliveryStatus } from '../../components/DeliveryStatus'
import { Icon } from '../../components/Icons'
import { LoadingMark } from '../../components/LoadingMark'
import { useResource } from '../../hooks/useResource'
import { REQUEST_STALE_AFTER_MS } from '../../hooks/resourceCache'
import { ProfileAvatar } from '../profile/ProfileAvatar'
import { CertificatePanel } from './EmploymentCertificate'
import { RequestReplyItem } from './RequestReplyItem'
import { RequestStatusLabel } from './RequestStatusLabel'
import { requestDateTime, workflow } from './requestPresentation'
import type { ReplyDraft } from './replyDrafts'
import { useCloudReplyDraft } from './useCloudReplyDraft'
import { useMessageAuthor } from './useMessageAuthor'

function useRequest(id: string) {
  const load = useCallback((signal: AbortSignal) => getRequest(id, signal), [id])
  return useResource(load, { key: `request:${id}`, staleAfterMs: REQUEST_STALE_AFTER_MS })
}

export function RequestDetail({
  id,
  documents,
  canOperate,
  canIssueCertificate,
  certificateRevision,
  conversation,
  onChanged,
  draft: localDraft,
  draftStorageError,
  onDraft,
}: {
  id: string
  documents: DocumentRecord[]
  canOperate: boolean
  canIssueCertificate: boolean
  certificateRevision: number
  conversation: boolean
  onChanged: () => void
  draft?: ReplyDraft
  draftStorageError: boolean
  onDraft: (id: string, draft: ReplyDraft | null, sent?: ReplyDraft) => void
}) {
  const { data: record, loading, refreshing, error, reload, replace } = useRequest(id)
  const author = useMessageAuthor(record?.author, record?.name ?? '', id)
  const certificate = record?.kind === 'employment_certificate'
  const watchOwnCertificate = certificate && record.certificate_owned && record.status !== 'resolved'
  useEffect(() => {
    if (!watchOwnCertificate) return
    let lastRevision: string | undefined
    return watchCertificateUpdates((revision) => {
      if (lastRevision === revision) return
      lastRevision = revision
      reload()
    }, reload, { requestId: id })
  }, [watchOwnCertificate, id, reload])
  const seenCertificateRevision = useRef(certificateRevision)
  useEffect(() => {
    if (!certificate || seenCertificateRevision.current === certificateRevision) return
    seenCertificateRevision.current = certificateRevision
    reload()
  }, [certificate, certificateRevision, reload])
  const loadDelivery = useCallback((signal: AbortSignal) => getDelivery(id, signal), [id])
  const delivery = useResource(loadDelivery, {
    key: `delivery:${id}`,
    enabled: Boolean(record) && !certificate,
    staleAfterMs: REQUEST_STALE_AFTER_MS,
  })
  const [pending, setPending] = useState<'status' | 'reply' | 'manage' | null>(null)
  const [statusError, setStatusError] = useState('')
  const [replyError, setReplyError] = useState('')
  const running = useRef(false)
  const activityHeading = useRef<HTMLHeadingElement>(null)
  const confirmed = Boolean(localDraft?.attemptId && record?.replies?.some(reply => reply.id === localDraft.attemptId))
  const cloud = useCloudReplyDraft(id, conversation && Boolean(record) && !certificate,
    record && !confirmed ? localDraft : undefined, onDraft)
  const draft = cloud.draft

  useEffect(() => {
    if (confirmed && localDraft) onDraft(id, null, localDraft)
  }, [confirmed, localDraft, id, onDraft])

  if (loading && !error) return <AsyncState title="Загружаем заявку…" loading />
  if (error && !record)
    return (
      <AsyncState
        title="Не удалось прочитать заявку"
        description={error}
        error
        onRetry={reload}
        retrying={loading || refreshing}
      />
    )
  if (!record) return null
  const document = documents.find((entry) => entry.id === record.document_id)
  const status = workflow[record.status]
  const certificateRefresh = certificate && record.certificate_downloadable &&
    (record.status !== 'resolved' || record.certificate_email_status)
  const text = draft?.text.trim() ?? ''
  const canReply = record.status !== 'resolved'
  const measuredText = conversation ? draft?.text ?? '' : text
  const tooLong = measuredText.length > MAX_REQUEST_REPLY_LENGTH && [...measuredText].length > MAX_REQUEST_REPLY_LENGTH
  const savingDraft = cloud.saving && !cloud.error && canReply

  function accept(updated: RequestRecord) {
    replace(updated)
    onChanged()
  }

  async function manageReply(replyId: string, replacement: string | null) {
    if (running.current) throw new ApiError('Дождитесь завершения текущего действия.')
    running.current = true
    setPending('manage')
    try {
      accept(await changeRequestReply(id, replyId, replacement))
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
      accept(await updateRequestStatus(id, record.status, status.next))
      setReplyError('')
    } catch (failure) {
      if (failure instanceof ApiError && failure.code === 'request_status_conflict') {
        reload()
        onChanged()
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
    if (running.current || !canReply || !draft || !text || tooLong || cloud.unsynced) return
    running.current = true
    setPending('reply')
    setReplyError('')
    try {
      const submission = { text: draft.text, attemptId: draft.attemptId ?? crypto.randomUUID() }
      if (!conversation) onDraft(id, submission)
      const updated = await replyToRequest(id, submission.attemptId, text)
      accept(updated)
      if (conversation) void cloud.clearAfterSend()
      else onDraft(id, null, submission)
      activityHeading.current?.focus()
    } catch (failure) {
      if (failure instanceof ApiError && failure.code === 'request_reply_conflict') {
        reload()
        onChanged()
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
    <article className="request-issue load-result" aria-labelledby="request-issue-title">
      <h1 id="request-issue-title">{conversation ? record.name : record.question}</h1>
      <aside className="request-properties" aria-labelledby="request-properties-title">
        <div className="request-properties-heading">
          <h2 id="request-properties-title">Свойства</h2>
          {!certificateRefresh && <button type="button" className="icon-button" aria-label="Обновить заявку" title="Обновить заявку"
            onClick={reload} disabled={refreshing || Boolean(pending)}>{refreshing && !error ? <LoadingMark compact /> : <Icon name="reload" />}</button>}
          <span className="refresh-status" role="status">{refreshing && !error ? 'Обновляем заявку…' : ''}</span>
        </div>
        {error && <AsyncState title="Не удалось обновить заявку" description={error} error inline onRetry={reload} retrying={loading || refreshing} />}
        <div className="request-workflow">
          <span role="status"><RequestStatusLabel record={record} /></span>
          {canOperate && !certificate && <button className="text-button" type="button" aria-disabled={Boolean(pending)} onClick={changeStatus}>
            {pending === 'status' && <LoadingMark compact />}
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
        {delivery.data && <DeliveryStatus delivery={delivery.data} onRefresh={delivery.reload}
          onRetried={delivery.replace} refreshing={delivery.refreshing} />}
        {!certificate && !delivery.error && delivery.loading && <p className="resource-update" role="status"><LoadingMark compact />Загружаем статус доставки…</p>}
        {!delivery.error && delivery.refreshing && <p className="resource-update" role="status"><LoadingMark compact />Обновляем статус доставки…</p>}
        {delivery.error && <AsyncState title="Статус доставки недоступен" description={delivery.error} error inline onRetry={delivery.reload} retrying={delivery.loading || delivery.refreshing} />}
      </aside>
      <section className="request-activity" aria-labelledby="request-activity-title">
        <h2 id="request-activity-title" ref={activityHeading} tabIndex={-1}>{conversation ? 'Переписка' : 'Активность'}</h2>
        <ol className="request-activity-list">
          <li className="request-activity-event">
            <ProfileAvatar className="request-avatar request-avatar-author" {...author} size={24} />
            <div><p><strong>{author.name}</strong>{!conversation && ' · создание заявки'}{record.created_at && <time className="thread-time" dateTime={record.created_at}>{requestDateTime.format(new Date(record.created_at))}</time>}</p>
              {conversation && <p className="incoming-message">{record.question}</p>}
            </div>
          </li>
          {record.replies?.map((reply) => (
            <RequestReplyItem key={reply.id} reply={reply} requestId={id} busy={Boolean(pending)}
              onChange={canOperate ? manageReply : undefined} onDeleted={() => activityHeading.current?.focus()} />
          ))}
        </ol>
        {certificate && <CertificatePanel record={record} canIssue={canIssueCertificate} onChanged={accept}
          refreshing={refreshing} refreshError={error} onRefresh={reload} />}
        {canOperate && !certificate && (canReply || draft) && (
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
                value={draft?.text ?? ''}
                readOnly={pending === 'reply' || cloud.loading}
                onChange={(event) => {
                  const value = event.target.value
                  if (conversation) {
                    onDraft(id, { text: value, attemptId: crypto.randomUUID(), baseRevision: cloud.revision })
                    return
                  }
                  onDraft(id, value ? { text: value, attemptId: value.trim() === text ? draft?.attemptId : undefined } : null)
                }}
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
              {draftStorageError && <p className="field-error" role="status">Браузер не смог сохранить изменения черновика.</p>}
              {conversation && cloud.error && <p className="field-error" role="status">
                {cloud.error}{' '}
                <button className="text-button" type="button" onClick={() => void cloud.retry()} disabled={cloud.saving}>
                  {cloud.saving && <LoadingMark compact />}
                  Повторить сохранение моей версии
                </button>
              </p>}
              <div className="request-composer-actions">
                <span className="refresh-status" role="status">{savingDraft ? 'Сохраняем черновик…' : ''}</span>
                <kbd aria-hidden="true">Ctrl ↵</kbd>
                <button className="button primary" type="submit" disabled={!canReply || !text || tooLong || Boolean(pending) || cloud.unsynced}>
                  {(pending === 'reply' || savingDraft) && <LoadingMark compact />}
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
