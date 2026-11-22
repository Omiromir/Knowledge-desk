import { useLayoutEffect, useRef, useSyncExternalStore } from 'react'
import type { DocumentRecord } from '../../api/types'
import { Icon } from '../../components/Icons'
import { requestsHref } from '../../navigation/routes'
import type { Member } from '../auth/AuthGate'
import { CertificateRequestForm } from './EmploymentCertificate'
import { RequestDetail } from './RequestDetail'
import { RequestForm } from './RequestForm'
import { RequestQueue } from './RequestQueue'
import { useReplyDraft } from './replyDrafts'
import { useRequestQueue } from './useRequestQueue'

const inboxMedia = window.matchMedia('(min-width: 1000px)')
function watchInboxWidth(notify: () => void) {
  inboxMedia.addEventListener('change', notify)
  return () => inboxMedia.removeEventListener('change', notify)
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
  const queue = useRequestQueue({
    visible: visible && (operator || (!requestId && !composeRequested)),
    remote: Boolean(role),
    watchQueue: visible && operator && Boolean(member?.can_issue_certificate),
    ownCertificateRevision,
    statusFilter,
    sortOrder,
  })
  const { activeFilter, oldestFirst, history, matches, count, certificateRevision, created } = queue
  const replyDraft = useReplyDraft(operator ? member?.user_id : undefined, requestId)
  const viewOptions = new URLSearchParams()
  if (activeFilter !== 'all') viewOptions.set('status', activeFilter)
  if (oldestFirst) viewOptions.set('sort', 'oldest')
  const viewQuery = viewOptions.toString()
  const requestListHref = requestsHref(viewQuery)
  const requestComposeHref = requestsHref(viewQuery, 'compose')

  return (
    <section className="requests-page" aria-label="Отправка и просмотр заявок">
      <header className="location-bar">
        {requestId || composeRequested ? <a className="text-button" href={requestListHref}>
          <Icon name="back" /> К списку заявок
        </a> : <><Icon name="inbox" /><h1>Заявки</h1></>}
        {((!requestId && !composeRequested) || (operator && wide)) && (
          <span className="request-history-count" role="status">
            {history.data !== null && `${matches.length} из ${count}`}
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
        <RequestQueue queue={queue} requestId={requestId} operator={operator} viewQuery={viewQuery}
          hidden={Boolean(requestId || composeRequested) && !operator} hasViewOptions={Boolean(statusFilter || sortOrder)}
          onClickCapture={(event) => {
            const link = event.target instanceof Element ? event.target.closest('a') : null
            if (link && !event.ctrlKey && !event.metaKey) returnRow.current = { link, top: window.scrollY }
          }} />
        <div className="request-form-pane" hidden={Boolean(requestId) || !composeRequested}>
          {certificateRequested ? <CertificateRequestForm onCreated={(record) => {
            created(record)
            const params = new URLSearchParams(viewQuery)
            params.set('id', record.id)
            window.location.hash = requestsHref(params)
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
            onDraft={replyDraft.changeDraft} onChanged={history.reload} />
        </div> : null}
      </div>
    </section>
  )
}
