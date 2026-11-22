import { Icon } from '../components/Icons'
import { LoadingMark } from '../components/LoadingMark'
import type { Member } from '../features/auth/AuthGate'
import { AccountProfileLink } from '../features/profile/AccountProfileLink'

type Props = {
  member?: Member
  roleLabel?: string
  onLogout?: () => void
  accountError?: string
  accountBusy?: boolean
  waitingForAnswer?: number
  catalogHref: string
  documentSearchHref: string
  requestListHref: string
  requestComposeHref: string
  requests: boolean
  asking: boolean
  profile: boolean
  toolsRoute: boolean
  consultationRoute: boolean
  searchRequested: boolean
}

export function DeskSidebar({
  member, roleLabel, onLogout, accountError, accountBusy, waitingForAnswer,
  catalogHref, documentSearchHref, requestListHref, requestComposeHref,
  requests, asking, profile, toolsRoute, consultationRoute, searchRequested,
}: Props) {
  const waiting = waitingForAnswer ?? 0
  return (
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
          {waitingForAnswer !== undefined && `Заявки, где нужен ваш ответ — ${waiting}`}
        </span>}
      </nav>
      {member && <div className="sidebar-account">
        <AccountProfileLink email={member.email} role={roleLabel} active={profile} />
        <button type="button" className="icon-button" aria-label={accountBusy ? 'Выходим…' : 'Выйти'} title="Выйти"
          disabled={accountBusy} onClick={onLogout}>{accountBusy ? <LoadingMark compact /> : <Icon name="exit" />}</button>
        {accountError && <p role="alert" className="field-error">{accountError}</p>}
      </div>}
    </header>
  )
}
