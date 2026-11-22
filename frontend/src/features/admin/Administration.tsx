import { useRef, useSyncExternalStore } from 'react'
import { Icon } from '../../components/Icons'
import { LoadingMark } from '../../components/LoadingMark'
import { profileHref, readHash } from '../../navigation/routes'
import type { Member } from '../auth/AuthGate'
import { ProfileWorkspace } from '../profile/ProfileWorkspace'
import { AdminWorkspace } from './AdminWorkspace'

function subscribeToRoute(callback: () => void) {
  window.addEventListener('hashchange', callback)
  return () => window.removeEventListener('hashchange', callback)
}

const getRoute = () => window.location.hash

export function Administration({ member, busy, error, onLogout, onPasswordChanged }: {
  member: Member
  busy: boolean
  error: string
  onLogout: () => void
  onPasswordChanged: () => void
}) {
  const main = useRef<HTMLElement>(null)
  const { section, parameters } = readHash(useSyncExternalStore(subscribeToRoute, getRoute))
  const profile = section === profileHref
  return (
    <div className="admin-shell">
      <a className="skip-link" href="#admin-content" onClick={(event) => {
        event.preventDefault()
        main.current?.focus()
      }}>Перейти к содержимому</a>
      <header className="admin-shell-bar">
        <div className="brand">
          <img className="brand-mark" src="/favicon.svg" width="25" height="25" alt="" />
          <span translate="no">Knowledge Desk</span>
        </div>
        <div className="admin-account-menu">
          <a className="account-profile-link" href={profileHref} aria-current={profile ? 'page' : undefined}>
            <span className="account-identity"><b>Мой профиль</b><span>Администратор</span></span>
          </a>
          <button type="button" className="icon-button" aria-label={busy ? 'Выходим…' : 'Выйти'} title="Выйти"
            disabled={busy} onClick={onLogout}>{busy ? <LoadingMark compact /> : <Icon name="exit" />}</button>
        </div>
        {error && <p className="field-error" role="alert">{error}</p>}
      </header>
      <main className="app-shell" id="admin-content" tabIndex={-1} ref={main}>
        {profile
          ? <ProfileWorkspace member={member} roleLabel="Администратор" onPasswordChanged={onPasswordChanged}
              security={parameters.get('tab') === 'security'} admin />
          : <AdminWorkspace />}
      </main>
    </div>
  )
}
