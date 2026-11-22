import { useRef, useState, type FormEvent } from 'react'
import {
  accessLabels, createMember, listMembers, setCertificateIssuer, setMemberAccess,
  type AccessPreset, type DirectoryMember, type MemberAccess,
} from '../../api/admin'
import { ApiError, errorMessage } from '../../api/client'
import { AsyncState } from '../../components/AsyncState'
import { Icon } from '../../components/Icons'
import { LoadingMark } from '../../components/LoadingMark'
import { useResource } from '../../hooks/useResource'
import './admin.css'

function AccessOptions() {
  return Object.entries(accessLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)
}

function AccessEditor({ member, busy, onSave, onCertificate }: {
  member: DirectoryMember
  busy: boolean
  onSave: (access: MemberAccess) => void
  onCertificate: (allowed: boolean) => void
}) {
  const [preset, setPreset] = useState<AccessPreset>(member.preset ?? 'reader')
  const changed = preset !== member.preset
  const hasQueue = member.preset === 'operator' || member.preset === 'editor_operator'

  return (
    <section className="admin-access" aria-labelledby="admin-account-title">
      <h2 id="admin-account-title">{member.email}</h2>
      <form onSubmit={(event) => {
        event.preventDefault()
        if (changed) onSave({ preset })
      }}>
        <div className="field">
          <label htmlFor="admin-preset">Набор прав</label>
          <select id="admin-preset" value={preset} disabled={busy}
            onChange={(event) => setPreset(event.target.value as AccessPreset)}>
            <AccessOptions />
          </select>
        </div>
        <button className="button primary" type="submit" disabled={busy || !changed}>Сохранить права</button>
      </form>
      <div className="admin-certificate">
        <h3>Выдача справок</h3>
        <p className="muted" role="status">
          {member.certificate_issuer
            ? hasQueue ? 'Допуск выдан' : 'Допуск выдан, нужен доступ к очереди'
            : hasQueue ? 'Допуск не выдан' : 'Для допуска нужен доступ к очереди'}
        </p>
        <button className="button secondary" type="button"
          disabled={busy || (!hasQueue && !member.certificate_issuer)}
          onClick={() => onCertificate(!member.certificate_issuer)}>
          {member.certificate_issuer ? 'Снять допуск' : 'Разрешить выдачу справок'}
        </button>
      </div>
    </section>
  )
}

export function AdminWorkspace() {
  const directory = useResource(listMembers)
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<string>()
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [creationError, setCreationError] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const running = useRef(false)
  const dialog = useRef<HTMLDialogElement>(null)
  const members = directory.data?.members ?? []
  const visible = members.filter(({ email }) => email.toLowerCase().includes(query.trim().toLowerCase()))
  const member = visible.find(({ user_id }) => user_id === selected) ?? visible[0]

  async function apply(operation: () => Promise<DirectoryMember>, success: string, creating = false) {
    if (running.current || !directory.data) return false
    running.current = true
    setBusy(true)
    setMessage('')
    setError('')
    setCreationError('')
    try {
      const updated = await operation()
      const members = directory.data.members.filter(({ user_id }) => user_id !== updated.user_id)
      directory.replace({
        ...directory.data,
        members: [...members, updated].sort((a, b) => a.email.localeCompare(b.email)),
      })
      setSelected(updated.user_id)
      if (creating) setQuery('')
      setMessage(success)
      return true
    } catch (failure) {
      if (creating) {
        const code = failure instanceof ApiError ? failure.code : undefined
        if (code === 'admin_account_without_access' || code === 'admin_creation_unconfirmed') {
          setCreationError('Проверьте список пользователей. Если аккаунт появился без доступа, назначьте ему права в карточке.')
        } else {
          setCreationError(errorMessage(failure))
        }
        directory.reload()
      } else {
        setError(errorMessage(failure))
      }
      return false
    } finally {
      running.current = false
      setBusy(false)
    }
  }

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const fields = new FormData(form)
    const created = await apply(() => createMember({
      email: String(fields.get('email')), password: String(fields.get('password')),
      preset: fields.get('preset') as AccessPreset,
    }), 'Аккаунт создан.', true)
    if (created) {
      form.reset()
      dialog.current?.close()
    }
  }

  return (
    <section className="admin-workspace" aria-labelledby="admin-title">
      <header className="location-bar">
        <Icon name="users" /><h1 id="admin-title">Пользователи</h1>
        <button className="button secondary admin-create" type="button" disabled={busy || !directory.data?.account_creation_enabled}
          onClick={() => { setCreationError(''); dialog.current?.showModal(); setCreateOpen(true) }}>
          <Icon name="plus" />Создать аккаунт
        </button>
      </header>
      {directory.loading && !directory.data && <AsyncState title="Загружаем пользователей…" loading />}
      {directory.error && <AsyncState title="Список пользователей недоступен" description={directory.error}
        error onRetry={directory.reload} retrying={directory.loading || directory.refreshing} />}
      {directory.data && !directory.error && (
        <>
          {!directory.data.account_creation_enabled && <p className="admin-notice muted">Создание аккаунтов не настроено на сервере.</p>}
          <div className="admin-layout">
            <div className="admin-list">
              <label className="search-field admin-search">
                <Icon name="search" />
                <input type="search" aria-label="Поиск пользователей по почте" placeholder="Поиск по почте…"
                  name="member-search" autoComplete="off" spellCheck={false}
                  value={query} onChange={(event) => setQuery(event.target.value)} />
              </label>
              {visible.length ? <ul className="admin-accounts">
                {visible.map((entry) => <li key={entry.user_id}>
                  <button type="button" className="admin-account" aria-pressed={entry.user_id === member?.user_id}
                    disabled={busy} onClick={() => { setSelected(entry.user_id); setMessage(''); setError('') }}>
                    <span className="account-avatar" aria-hidden="true">{entry.email.slice(0, 1).toUpperCase()}</span>
                    <span className="admin-account-copy">
                      <strong title={entry.email}>{entry.email}</strong>
                      <span>{entry.preset ? accessLabels[entry.preset] : 'Без доступа'}</span>
                    </span>
                  </button>
                </li>)}
              </ul> : <p className="admin-notice muted">{query.trim() ? 'Пользователи не найдены' : 'Пользователей пока нет'}</p>}
            </div>
            {member && <div className="admin-inspector">
              <AccessEditor key={`${member.user_id}:${member.preset}`} member={member}
                busy={busy} onSave={(access) => void apply(() => setMemberAccess(member.user_id, access), 'Права сохранены.')}
                onCertificate={(allowed) => void apply(() => setCertificateIssuer(member.user_id, allowed), '')} />
              {error && <p className="field-error" role="alert">{error}</p>}
              <p className="admin-result muted" role="status">{busy && !createOpen && <LoadingMark compact />}{message}</p>
            </div>}
          </div>
        </>
      )}
      <dialog className="document-dialog admin-dialog" ref={dialog} aria-labelledby="admin-create-title"
        onClose={() => { setCreateOpen(false); dialog.current?.querySelector('form')?.reset() }}
        onCancel={(event) => { if (busy) event.preventDefault() }}>
        <form onSubmit={create}>
          <h2 id="admin-create-title">Создать аккаунт</h2>
          <div className="field">
            <label htmlFor="admin-create-email">Почта</label>
            <input id="admin-create-email" name="email" type="email" autoComplete="off" spellCheck={false}
              maxLength={254} required disabled={busy} />
          </div>
          <div className="field">
            <label htmlFor="admin-create-password">Пароль</label>
            <input id="admin-create-password" name="password" type="password" autoComplete="new-password"
              minLength={12} maxLength={72} required disabled={busy} aria-describedby="admin-password-hint" />
            <p className="muted" id="admin-password-hint">От 12 символов. Передайте пароль сотруднику лично.</p>
          </div>
          <div className="field">
            <label htmlFor="admin-create-preset">Набор прав</label>
            <select id="admin-create-preset" name="preset" defaultValue="reader" disabled={busy}><AccessOptions /></select>
          </div>
          {creationError && <p className="field-error" role="alert">{creationError}</p>}
          <div className="document-dialog-actions">
            <button className="button secondary" type="button" disabled={busy} onClick={() => dialog.current?.close()}>Отмена</button>
            <button className="button primary" type="submit" disabled={busy}>
              {busy && <LoadingMark compact />}{busy ? 'Создаём…' : 'Создать'}
            </button>
          </div>
        </form>
      </dialog>
    </section>
  )
}
