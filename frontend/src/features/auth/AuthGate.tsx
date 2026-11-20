import { useEffect, useState, type FormEvent } from 'react'
import App from '../../App'
import { ApiError, errorMessage, requestJson } from '../../api/client'
import { isObject } from '../../api/types'
import { AsyncState } from '../../components/AsyncState'

const roles = { reader: 'Читатель', editor: 'Редактор', operator: 'Оператор' } as const
export type Member = { user_id: string; email: string; role: keyof typeof roles }
type Session = { mode: 'local' | 'supabase'; member: Member | null }

function sessionFrom(value: unknown): Session {
  if (!isObject(value) || (value.mode !== 'local' && value.mode !== 'supabase'))
    throw new ApiError('Не удалось проверить вход.')
  const member = value.member
  if (member === null) return { mode: value.mode, member: null }
  if (!isObject(member) || typeof member.user_id !== 'string' || typeof member.email !== 'string' ||
    !Object.keys(roles).some((role) => role === member.role)) throw new ApiError('Не удалось определить роль.')
  return { mode: value.mode, member: member as Member }
}

export function AuthGate() {
  const [session, setSession] = useState<Session | null>(null)
  const [revision, setRevision] = useState(0)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    requestJson('/auth/session', { signal: controller.signal }).then(sessionFrom).then(setSession).catch((failure: unknown) => {
      if (!controller.signal.aborted) setError(errorMessage(failure))
    })
    function expired() {
      setSession({ mode: 'supabase', member: null })
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
    try {
      const value = await requestJson('/auth/login', { method: 'POST', body: JSON.stringify(Object.fromEntries(credentials)) })
      form.reset()
      setSession(sessionFrom(value))
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      setBusy(false)
    }
  }

  async function logout() {
    try {
      await requestJson('/auth/logout', { method: 'POST' })
      setSession({ mode: 'supabase', member: null })
      setError('')
    } catch (failure) {
      setError(errorMessage(failure))
    }
  }

  if (session?.mode === 'local') return <App />
  if (session?.member) return <App key={session.member.user_id} member={session.member} roleLabel={roles[session.member.role]}
    onLogout={() => void logout()} accountError={error} />

  return (
    <main className="auth-screen">
      <div className="auth-panel">
        <div className="brand"><span className="brand-mark" aria-hidden="true">K</span> Knowledge Desk</div>
        {session ? (
          <form className="auth-form" onSubmit={login}>
            <h1>Вход в систему</h1>
            <label className="field">Почта<input name="email" type="email" autoComplete="username" spellCheck={false} required /></label>
            <label className="field">Пароль<input name="password" type="password" autoComplete="current-password" required /></label>
            {error && <p role="alert" className="field-error">{error}</p>}
            <button type="submit" className="button primary" disabled={busy}>{busy ? 'Входим…' : 'Войти'}</button>
          </form>
        ) : error ? (
          <AsyncState title="Вход недоступен" description={error} error onRetry={() => { setError(''); setRevision(revision + 1) }} />
        ) : <AsyncState title="Проверяем вход…" loading />}
      </div>
    </main>
  )
}
