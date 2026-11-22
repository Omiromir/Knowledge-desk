import { useEffect, useState, type FormEvent } from 'react'
import App from '../../App'
import { ApiError, errorMessage, requestJson } from '../../api/client'
import { isObject } from '../../api/types'
import { AsyncState } from '../../components/AsyncState'
import { Icon } from '../../components/Icons'
import { LoadingMark } from '../../components/LoadingMark'
import { Administration } from '../admin/Administration'
import { CurrentProfileProvider } from '../profile/CurrentProfile'
import { AuthBackdrop } from './AuthBackdrop'
import './auth.css'

const roles = { reader: 'Читатель', editor: 'Редактор', operator: 'Оператор', admin: 'Администратор' } as const
export type Member = {
  user_id: string
  email: string
  role: keyof typeof roles
  can_operate_requests: boolean
  can_request_certificate?: boolean
  can_issue_certificate?: boolean
}
type Session = { mode: 'local' | 'supabase'; member: Member | null }

function sessionFrom(value: unknown): Session {
  if (!isObject(value) || (value.mode !== 'local' && value.mode !== 'supabase'))
    throw new ApiError('Не удалось проверить вход.')
  const member = value.member
  if (member === null) return { mode: value.mode, member: null }
  if (!isObject(member) || typeof member.user_id !== 'string' || typeof member.email !== 'string' ||
    (member.can_operate_requests !== undefined && typeof member.can_operate_requests !== 'boolean') ||
    !Object.keys(roles).some((role) => role === member.role)) throw new ApiError('Не удалось определить роль.')
  const canOperate = member.can_operate_requests ?? (member.role === 'operator')
  return { mode: value.mode, member: { ...member, can_operate_requests: canOperate } as Member }
}

export function AuthGate() {
  const [session, setSession] = useState<Session | null>(null)
  const [revision, setRevision] = useState(0)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [retrying, setRetrying] = useState(false)
  const [busy, setBusy] = useState(false)
  const [showPassword, setShowPassword] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    requestJson('/auth/session', { signal: controller.signal })
      .then(sessionFrom)
      .then((nextSession) => {
        if (controller.signal.aborted) return
        setSession(nextSession)
        setError('')
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(failure))
      })
      .finally(() => {
        if (!controller.signal.aborted) setRetrying(false)
      })
    function expired() {
      setSession({ mode: 'supabase', member: null })
      setNotice('')
      setError('Сессия закончилась. Войди снова.')
    }
    window.addEventListener('knowledge-desk-session-expired', expired)
    return () => {
      window.removeEventListener('knowledge-desk-session-expired', expired)
      controller.abort()
    }
  }, [revision])

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    const form = event.currentTarget
    const credentials = new FormData(form)
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const value = await requestJson('/auth/login', { method: 'POST', body: JSON.stringify(Object.fromEntries(credentials)) })
      form.reset()
      setShowPassword(false)
      setSession(sessionFrom(value))
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      setBusy(false)
    }
  }

  function passwordChanged() {
    setSession({ mode: 'supabase', member: null })
    setError('')
    setNotice('Пароль изменён. Войдите с новым паролем.')
  }

  async function logout() {
    if (busy) return
    setBusy(true)
    try {
      await requestJson('/auth/logout', { method: 'POST' })
      setSession({ mode: 'supabase', member: null })
      setError('')
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      setBusy(false)
    }
  }

  if (session?.mode === 'local') return <App />
  if (session?.member) {
    const { member } = session
    const roleLabel = member.role === 'editor' && member.can_operate_requests ? 'Редактор и оператор' : roles[member.role]
    return <CurrentProfileProvider key={member.user_id}>
      {member.role === 'admin'
        ? <Administration member={member} busy={busy} error={error}
            onLogout={() => void logout()} onPasswordChanged={passwordChanged} />
        : <App key={`${member.role}:${member.can_operate_requests}`} member={member} roleLabel={roleLabel}
            onLogout={() => void logout()} accountError={error} accountBusy={busy} onPasswordChanged={passwordChanged} />}
    </CurrentProfileProvider>
  }

  return (
    <main className="auth-screen" aria-labelledby="auth-title">
      <AuthBackdrop />
      <div className="auth-panel">
        <h1 id="auth-title" className="auth-sr-text">Вход в Knowledge Desk</h1>
        {session ? (
          <form className="auth-form load-result" onSubmit={login}>
            <div className="auth-fields">
              <div className="auth-field">
                <label htmlFor="auth-email">Почта</label>
                <input id="auth-email" name="email" type="email" autoComplete="username" placeholder=" "
                  spellCheck={false} autoCapitalize="none" readOnly={busy} required />
              </div>
              <div className="auth-field auth-password">
                <label htmlFor="auth-password">Пароль</label>
                <input id="auth-password" name="password" type={showPassword ? 'text' : 'password'} placeholder=" "
                  autoComplete="current-password" spellCheck={false} autoCapitalize="none" readOnly={busy} required />
                <button className="icon-button" type="button" aria-controls="auth-password"
                  aria-label={showPassword ? 'Скрыть пароль' : 'Показать пароль'}
                  onClick={() => setShowPassword((visible) => !visible)}>
                  <Icon name={showPassword ? 'eyeOff' : 'eye'} />
                </button>
              </div>
            </div>
            {error && <p role="alert" className="auth-error">{error}</p>}
            {notice && <p role="status" className="auth-notice">{notice}</p>}
            <button type="submit" className="button auth-submit" disabled={busy}>
              {busy && <LoadingMark compact />}
              {busy ? 'Входим…' : 'Войти'}
            </button>
            <span className="auth-sr-text" role="status">{busy ? 'Проверяем данные для входа…' : ''}</span>
          </form>
        ) : error ? (
          <AsyncState title="Вход недоступен" description={error} error retrying={retrying}
            onRetry={() => { setRetrying(true); setRevision((value) => value + 1) }} />
        ) : <AsyncState title="Проверяем вход…" loading />}
      </div>
    </main>
  )
}
