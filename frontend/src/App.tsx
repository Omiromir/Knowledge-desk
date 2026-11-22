import { useContext, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { watchOwnCertificateUpdates, type OwnCertificateUpdate } from './api/certificates'
import { listDocuments } from './api/documents'
import { consultationHref } from './api/consultations'
import { Icon } from './components/Icons'
import { LoadingMark } from './components/LoadingMark'
import { AskWorkspace } from './features/ask/AskWorkspace'
import { ConsultationWorkspace } from './features/ask/ConsultationWorkspace'
import { ToolWorkspace } from './features/ask/ToolWorkspace'
import { DocumentBrowser } from './features/documents/DocumentBrowser'
import { DocumentReader } from './features/documents/DocumentReader'
import { ProfileWorkspace, profileHref } from './features/profile/ProfileWorkspace'
import { AccountProfileLink } from './features/profile/AccountProfileLink'
import { readRequestPreference, saveRequestPreference } from './features/requests/history'
import { RequestWorkspace } from './features/requests/RequestWorkspace'
import { useResource } from './hooks/useResource'
import { ResourceCache } from './hooks/resourceCache'
import type { Member } from './features/auth/AuthGate'

function readRoute(hash: string, requestView = '', graphView = false) {
  const [section, query] = hash.split('?', 2)
  const parameters = new URLSearchParams(query)
  if (section === '#requests') {
    const view = new URLSearchParams()
    for (const key of ['status', 'sort']) {
      const value = parameters.get(key)
      if (value) view.set(key, value)
    }
    requestView = view.toString()
  }
  if (section === '#documents') graphView = parameters.get('view') === 'graph'
  return { section, parameters, requestView, graphView }
}

type Props = {
  member?: Member; roleLabel?: string; onLogout?: () => void; accountError?: string; accountBusy?: boolean
  onPasswordChanged?: () => void
}

export default function App(props: Props) {
  const [cache] = useState(() => new Map<string, { data: unknown; loadedAt: number }>())
  return <ResourceCache.Provider value={cache}><Desk {...props} /></ResourceCache.Provider>
}

function Desk({ member, roleLabel, onLogout, accountError, accountBusy, onPasswordChanged }: Props) {
  const cache = useContext(ResourceCache)
  const [{ section, parameters, requestView, graphView }, setRoute] = useState(() =>
    readRoute(window.location.hash, readRequestPreference('view')),
  )
  const [suggestedQuestion, setSuggestedQuestion] = useState('')
  const [conversationRevision, setConversationRevision] = useState(0)
  const [catalogRevision, setCatalogRevision] = useState(0)
  const [ownCertificateUpdate, setOwnCertificateUpdate] = useState<OwnCertificateUpdate | null>(null)
  const memberId = member?.user_id
  const waiting = ownCertificateUpdate?.waiting ?? 0
  const requests = section === '#requests'
  const asking = section === '#ask'
  const profile = section === profileHref && Boolean(member && onPasswordChanged)
  const requestListHref = requestView ? `#requests?${requestView}` : '#requests'
  const requestComposeHref = requestView ? `#requests?compose&${requestView}` : '#requests?compose'
  const askSourceId = asking ? parameters.get('chunk') : null
  const toolsRoute = asking && parameters.has('tools')
  const consultationRoute = asking && parameters.has('consultation')
  const consultationId = consultationRoute ? parameters.get('consultation') || null : null
  const usingConsultation = consultationRoute && !askSourceId
  const usingTools = toolsRoute && !askSourceId && !usingConsultation
  const sourceBack = consultationRoute
    ? { href: consultationHref(consultationId ?? undefined), label: 'К консультации' }
    : toolsRoute ? { href: '#ask?tools', label: 'К помощнику' } : { href: '#ask', label: 'К ответу' }
  const documentId = section === '#documents' ? parameters.get('id') : null
  const localGraph = Boolean(documentId && parameters.has('local'))
  const depth = parameters.get('depth')
  const graphDepth = depth === '3' ? 3 : depth === '2' ? 2 : 1
  const catalogHref = graphView ? '#documents?view=graph' : '#documents'
  const documentSearchHref = graphView ? '#documents?view=graph&search' : '#documents?search'
  const requestDocumentId = requests ? parameters.get('document') : null
  const requestId = requests ? parameters.get('id') : null
  const composeRequested = requests && parameters.has('compose')
  const certificateRequested = composeRequested && parameters.has('certificate') && Boolean(member?.can_request_certificate)
  const searchRequested = section === '#documents' && parameters.has('search')
  const browsing = !requests && !asking && !documentId && !profile
  const needsCatalog = browsing || localGraph || Boolean(requestDocumentId) || (asking && !toolsRoute && !consultationRoute && !askSourceId)
  const documents = useResource(listDocuments, { key: 'documents', enabled: needsCatalog, recheckOn: section })
  const backLink = useRef<HTMLAnchorElement>(null)
  const requestPage = useRef<HTMLDivElement>(null)
  const lastRequest = useRef(requestId)

  useEffect(() => {
    if (memberId) return watchOwnCertificateUpdates(setOwnCertificateUpdate)
  }, [memberId])

  function newConversation() {
    setConversationRevision((revision) => revision + 1)
    setSuggestedQuestion('')
    window.location.hash = '#ask?tools'
  }

  function catalogChanged() {
    for (const key of cache?.keys() ?? []) {
      if (key.startsWith('source:')) cache?.delete(key)
    }
    setCatalogRevision((revision) => revision + 1)
  }

  useEffect(() => {
    saveRequestPreference('view', requestView)
  }, [requestView])

  useEffect(() => {
    const update = (event: HashChangeEvent) => {
      const hash = new URL(event.newURL).hash
      setRoute((previous) => readRoute(hash, previous.requestView, previous.graphView))
    }
    const search = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.code !== 'KeyK') return
      event.preventDefault()
      if (window.location.hash === documentSearchHref) {
        document.getElementById('document-search')?.focus()
      } else {
        window.location.hash = documentSearchHref
      }
    }
    window.addEventListener('hashchange', update)
    window.addEventListener('keydown', search)
    return () => {
      window.removeEventListener('hashchange', update)
      window.removeEventListener('keydown', search)
    }
  }, [documentSearchHref])

  useLayoutEffect(() => {
    const returningToQueue = requests && lastRequest.current && !requestId && !composeRequested
    lastRequest.current = requestId
    if (!returningToQueue && (requests || asking || documentId || profile)) window.scrollTo(0, 0)
    if (returningToQueue && !requestPage.current?.contains(document.activeElement)) {
      requestPage.current?.focus({ preventScroll: true })
    }
    if (documentId) {
      backLink.current?.focus({ preventScroll: true })
    }
    if (requestId) requestPage.current?.querySelector<HTMLElement>('.thread-pane')?.focus({ preventScroll: true })
    if (requestDocumentId) requestPage.current?.focus({ preventScroll: true })
    if (composeRequested) document.getElementById(certificateRequested ? 'certificate-purpose' : 'request-name')?.focus()
    if (searchRequested) document.getElementById('document-search')?.focus()
  }, [
    documentId,
    asking,
    askSourceId,
    usingTools,
    usingConsultation,
    consultationId,
    requests,
    requestDocumentId,
    requestId,
    composeRequested,
    certificateRequested,
    searchRequested,
    profile,
  ])

  return (
    <div className="desk-layout">
      <a
        className="skip-link"
        href="#main-content"
        onClick={(event) => {
          event.preventDefault()
          document.getElementById('main-content')?.focus()
        }}
      >
        Перейти к содержимому
      </a>
      <header className="desk-sidebar">
        <div className="sidebar-heading">
          <a
            className="brand"
            href={catalogHref}
            aria-label="Knowledge Desk: документы"
          >
            <img className="brand-mark" src="/favicon.svg" width="25" height="25" alt="" />
            <span translate="no">Knowledge Desk</span>
          </a>
          {!requests && <a
            className="icon-button compose-button"
            href={requestComposeHref}
            aria-label="Новая заявка"
            title="Новая заявка"
          >
            <Icon name="compose" />
          </a>}
        </div>
        <a className="sidebar-search" href={documentSearchHref} aria-label="Поиск документов"
          onClick={() => { if (searchRequested) document.getElementById('document-search')?.focus() }}>
          <Icon name="search" />Поиск<kbd>{/Mac/i.test(navigator.platform) ? '⌘ K' : 'Ctrl K'}</kbd>
        </a>
        <nav className="primary-nav" aria-label="Основная навигация">
          <a
            href={catalogHref}
            aria-current={!requests && !asking && !profile ? 'page' : undefined}
          >
            <Icon name="file" />
            Документы
          </a>
          <a href="#ask" data-section-active={asking || undefined} aria-current={asking && !toolsRoute && !consultationRoute ? 'page' : undefined}>
            <Icon name="chat" />
            Ответы
          </a>
          <div className="answer-nav">
            <a href="#ask?tools" aria-current={toolsRoute ? 'page' : undefined}>Помощник</a>
            <a href="#ask?consultation" aria-current={consultationRoute ? 'page' : undefined}>Консультация</a>
          </div>
          <a href={requestListHref} aria-current={requests ? 'page' : undefined}
            aria-describedby={waiting > 0 ? 'request-nav-status' : undefined}>
            <Icon name="inbox" />
            <span className="request-nav-label">
              Заявки
              {waiting > 0 && <span className="request-nav-count" aria-hidden="true">{waiting}</span>}
            </span>
          </a>
          {member && <span id="request-nav-status" className="request-nav-status" role="status">
            {ownCertificateUpdate && `Заявки, где нужен ваш ответ — ${waiting}`}
          </span>}
        </nav>
        {member && <div className="sidebar-account">
          <AccountProfileLink email={member.email} role={roleLabel} active={profile} />
          <button type="button" className="icon-button" aria-label={accountBusy ? 'Выходим…' : 'Выйти'} title="Выйти"
            disabled={accountBusy} onClick={onLogout}>{accountBusy ? <LoadingMark compact /> : <Icon name="exit" />}</button>
          {accountError && <p role="alert" className="field-error">{accountError}</p>}
        </div>}
      </header>
      <main className="app-shell" id="main-content" tabIndex={-1}>
        {profile && member && onPasswordChanged && (
          <ProfileWorkspace member={member} roleLabel={roleLabel ?? ''} onPasswordChanged={onPasswordChanged}
            security={parameters.get('tab') === 'security'} />
        )}
        <DocumentBrowser
          resource={documents}
          hidden={!browsing}
          graphView={graphView}
          requestComposeHref={requestComposeHref}
          onUploaded={catalogChanged}
          canUpload={!member || member.role === 'editor'}
        />
        {documentId && !requests ? (
          <DocumentReader
            key={documentId}
            id={documentId}
            backLink={backLink}
            catalogHref={catalogHref}
            documentSearchHref={documentSearchHref}
            catalog={documents}
            localGraph={localGraph}
            graphDepth={graphDepth}
            requestView={requestView}
            role={member?.role}
            onChanged={() => {
              documents.reload()
              catalogChanged()
            }}
          />
        ) : null}
        <div hidden={!asking || usingTools || usingConsultation}>
          <AskWorkspace
            key={conversationRevision}
            documents={documents.data ?? []}
            catalogRevision={catalogRevision}
            sourceId={askSourceId}
            sourceBack={sourceBack}
            onPrepareRequest={(text) => {
              setSuggestedQuestion(text)
              window.location.hash = requestComposeHref
            }}
          />
        </div>
        <div hidden={!usingTools}>
          <ToolWorkspace key={conversationRevision} onNewConversation={newConversation} />
        </div>
        <div hidden={!usingConsultation}>
          <ConsultationWorkspace key={conversationRevision} draftId={consultationId} onNewConversation={newConversation} />
        </div>
        <div
          className="requests-content"
          hidden={!requests}
          ref={requestPage}
          tabIndex={-1}
        >
          <RequestWorkspace
            visible={requests}
            member={member}
            ownCertificateRevision={member?.can_issue_certificate ? undefined : ownCertificateUpdate?.revision}
            requestId={requestId}
            composeRequested={composeRequested || Boolean(requestDocumentId)}
            certificateRequested={certificateRequested}
            statusFilter={parameters.get('status')}
            sortOrder={parameters.get('sort')}
            documentId={requestDocumentId}
            documents={documents.data ?? []}
            documentsLoading={documents.loading}
            suggestedQuestion={suggestedQuestion}
            onUseSuggestedQuestion={() => setSuggestedQuestion('')}
          />
        </div>
      </main>
    </div>
  )
}
