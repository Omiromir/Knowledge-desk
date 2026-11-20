import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { listDocuments } from './api/documents'
import { consultationHref } from './api/consultations'
import { Icon } from './components/Icons'
import { AskWorkspace } from './features/ask/AskWorkspace'
import { ConsultationWorkspace } from './features/ask/ConsultationWorkspace'
import { ToolWorkspace } from './features/ask/ToolWorkspace'
import { DocumentBrowser } from './features/documents/DocumentBrowser'
import { DocumentReader } from './features/documents/DocumentReader'
import { readRequestPreference, saveRequestPreference } from './features/requests/history'
import { RequestWorkspace } from './features/requests/RequestWorkspace'
import { useResource } from './hooks/useResource'
import type { Member } from './features/auth/AuthGate'

function readRoute(hash: string, requestView = '') {
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
  return { section, parameters, requestView }
}

export default function App({ member, roleLabel, onLogout, accountError }: {
  member?: Member; roleLabel?: string; onLogout?: () => void; accountError?: string
} = {}) {
  const [{ section, parameters, requestView }, setRoute] = useState(() =>
    readRoute(window.location.hash, readRequestPreference('view')),
  )
  const [suggestedQuestion, setSuggestedQuestion] = useState('')
  const [conversationRevision, setConversationRevision] = useState(0)
  const [catalogRevision, setCatalogRevision] = useState(0)
  const requests = section === '#requests'
  const asking = section === '#ask'
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
  const requestDocumentId = requests ? parameters.get('document') : null
  const requestId = requests ? parameters.get('id') : null
  const composeRequested = requests && parameters.has('compose')
  const searchRequested = section === '#documents' && parameters.has('search')
  const documents = useResource(listDocuments)
  const backLink = useRef<HTMLAnchorElement>(null)
  const requestPage = useRef<HTMLDivElement>(null)

  function newConversation() {
    setConversationRevision((revision) => revision + 1)
    setSuggestedQuestion('')
    window.location.hash = '#ask?tools'
  }

  useEffect(() => {
    saveRequestPreference('view', requestView)
  }, [requestView])

  useEffect(() => {
    const update = (event: HashChangeEvent) => {
      const hash = new URL(event.newURL).hash
      setRoute((previous) => readRoute(hash, previous.requestView))
    }
    const search = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.code !== 'KeyK') return
      event.preventDefault()
      if (window.location.hash === '#documents?search') {
        document.getElementById('document-search')?.focus()
      } else {
        window.location.hash = '#documents?search'
      }
    }
    window.addEventListener('hashchange', update)
    window.addEventListener('keydown', search)
    return () => {
      window.removeEventListener('hashchange', update)
      window.removeEventListener('keydown', search)
    }
  }, [])

  useLayoutEffect(() => {
    if (requests || asking || documentId) window.scrollTo(0, 0)
    if (documentId) {
      backLink.current?.focus({ preventScroll: true })
    }
    if (requestDocumentId || requestId) requestPage.current?.focus({ preventScroll: true })
    if (composeRequested) document.getElementById('request-name')?.focus()
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
    searchRequested,
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
            href="#documents"
            aria-label="Knowledge Desk: документы"
          >
            <span className="brand-mark" aria-hidden="true">
              K
            </span>
            <span translate="no">Knowledge Desk</span>
          </a>
          <a
            className="icon-button sidebar-search"
            href="#documents?search"
            aria-label="Поиск документов"
            title="Поиск документов"
            onClick={() => {
              if (searchRequested)
                document.getElementById('document-search')?.focus()
            }}
          >
            <Icon name="search" />
          </a>
          <a
            className="icon-button compose-button"
            href={requestComposeHref}
            aria-label="Новая заявка"
            title="Новая заявка"
            onClick={() => {
              if (composeRequested)
                document.getElementById('request-name')?.focus()
            }}
          >
            <Icon name="compose" />
          </a>
        </div>
        <nav className="primary-nav" aria-label="Основная навигация">
          <a
            href="#documents"
            aria-current={!requests && !asking ? 'page' : undefined}
          >
            <Icon name="file" />
            Документы
          </a>
          <a href="#ask" aria-current={asking ? 'page' : undefined}>
            <Icon name="search" />
            Ответы
          </a>
          <a href={requestListHref} aria-current={requests ? 'page' : undefined}>
            <Icon name="inbox" />
            Заявки
          </a>
        </nav>
        {member && <div className="sidebar-account">
          <span title={member.email}>{roleLabel}</span>
          <button type="button" className="text-button" onClick={onLogout}>Выйти</button>
          {accountError && <p role="alert" className="field-error">{accountError}</p>}
        </div>}
      </header>
      <main className="app-shell" id="main-content" tabIndex={-1}>
        <DocumentBrowser
          resource={documents}
          hidden={requests || asking || Boolean(documentId)}
          requestComposeHref={requestComposeHref}
          onUploaded={() => setCatalogRevision((revision) => revision + 1)}
          canUpload={!member || member.role === 'editor'}
        />
        {documentId && !requests ? (
          <DocumentReader
            key={documentId}
            id={documentId}
            backLink={backLink}
            requestView={requestView}
            role={member?.role}
            onChanged={() => {
              documents.reload()
              setCatalogRevision((revision) => revision + 1)
            }}
          />
        ) : null}
        <div hidden={!asking || usingTools || usingConsultation}>
          <AskWorkspace
            key={conversationRevision}
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
            role={member?.role}
            requestId={requestId}
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
