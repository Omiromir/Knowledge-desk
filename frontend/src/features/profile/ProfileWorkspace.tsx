import { useContext, useRef, useState, type FormEvent } from 'react'
import { ApiError, errorMessage } from '../../api/client'
import { avatarUrl, changePassword, removeAvatar, saveDisplayName, uploadAvatar } from '../../api/profile'
import { AsyncState } from '../../components/AsyncState'
import { Icon } from '../../components/Icons'
import { LoadingMark } from '../../components/LoadingMark'
import type { Member } from '../auth/AuthGate'
import { CurrentProfile } from './currentProfileContext'
import { ProfileAvatar } from './ProfileAvatar'
import './profile.css'

export const profileHref = '#profile'
const securityHref = '#profile?tab=security'

type Props = {
  member: Member
  roleLabel: string
  onPasswordChanged: () => void
  admin?: boolean
  security?: boolean
}

export function ProfileWorkspace({ member, roleLabel, onPasswordChanged, admin, security }: Props) {
  const resource = useContext(CurrentProfile)
  const [editedName, setEditedName] = useState<string | null>(null)
  const [busy, setBusy] = useState<'name' | 'upload' | 'remove' | null>(null)
  const [feedback, setFeedback] = useState<{ type: 'status' | 'alert'; message: string } | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  if (!resource) return null
  const { replace } = resource
  const profile = resource.data
  const name = editedName ?? profile?.display_name ?? ''
  const trimmedName = name.trim()

  async function saveName(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !profile || trimmedName === profile.display_name) return
    setBusy('name')
    setFeedback(null)
    try {
      replace(await saveDisplayName(trimmedName))
      setEditedName(null)
      setFeedback({ type: 'status', message: 'Имя сохранено.' })
    } catch (failure) {
      const message = failure instanceof ApiError && failure.status === 422
        ? 'Имя должно быть не длиннее 60 символов и содержать только печатные символы.'
        : errorMessage(failure)
      setFeedback({ type: 'alert', message })
    } finally {
      setBusy(null)
    }
  }

  async function savePhoto(file?: File) {
    if (busy) return
    if (file && file.size > 2 * 1024 * 1024) {
      setFeedback({ type: 'alert', message: 'Размер фото должен быть не больше 2 МБ.' })
      return
    }
    setBusy(file ? 'upload' : 'remove')
    setFeedback(null)
    try {
      replace(await (file ? uploadAvatar(file) : removeAvatar()))
      setFeedback({ type: 'status', message: file ? 'Фото обновлено.' : 'Фото удалено.' })
    } catch (failure) {
      setFeedback({ type: 'alert', message: errorMessage(failure) })
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="profile-workspace">
      <header className="profile-heading">
        {admin && <a className="text-button profile-back" href="#admin"><Icon name="back" /> К пользователям</a>}
        <h1>Мой профиль</h1>
      </header>
      {profile ? (
        <div className="profile-settings">
          <nav className="profile-navigation" aria-label="Настройки профиля">
            <a href={profileHref} aria-current={!security ? 'page' : undefined}><Icon name="user" /> Личные данные</a>
            <a href={securityHref} aria-current={security ? 'page' : undefined}><Icon name="lock" /> Безопасность</a>
          </nav>
          <section className="profile-content load-result" aria-label="Личные данные" hidden={security}>
            <div className="profile-identity">
              <div className="profile-cover" aria-hidden="true" />
              <div className="profile-identity-body">
                <div className="profile-identity-copy">
                  <ProfileAvatar className="profile-avatar" name={profile.display_name || member.email} size={72}
                    src={profile.avatar_version ? avatarUrl(profile.avatar_version) : undefined} />
                  <h2>{profile.display_name || member.email}</h2>
                  {profile.display_name && <p className="muted">{member.email}</p>}
                  <p className="profile-role">{roleLabel}</p>
                </div>
                <div className="profile-photo-details">
                  <div className="profile-actions">
                    <input hidden ref={fileInput} type="file" accept="image/jpeg,image/png,image/webp"
                      onChange={(event) => {
                        const file = event.currentTarget.files?.[0]
                        event.currentTarget.value = ''
                        if (file) void savePhoto(file)
                      }} />
                    <button className="button secondary" type="button" disabled={busy !== null}
                      aria-describedby="profile-photo-hint" onClick={() => fileInput.current?.click()}>
                      {busy === 'upload' && <LoadingMark compact />}
                      {profile.avatar_version ? 'Заменить фото' : 'Загрузить фото'}
                    </button>
                    {profile.avatar_version && <button className="text-button" type="button" disabled={busy !== null}
                      onClick={() => { if (window.confirm('Удалить фото профиля?')) void savePhoto() }}>
                      {busy === 'remove' && <LoadingMark compact />} Удалить фото
                    </button>}
                  </div>
                  <p className="muted" id="profile-photo-hint">JPG, PNG или WebP до 2&nbsp;МБ</p>
                </div>
              </div>
            </div>
            <form className="profile-card profile-name-form" onSubmit={saveName}>
              <label className="field">
                <span>Отображаемое имя</span>
                <input name="name" autoComplete="name"
                  maxLength={60} value={name} readOnly={busy === 'name'}
                  onChange={(event) => setEditedName(event.target.value)} />
              </label>
              <div className="profile-card-footer">
                <div className="profile-feedback">
                  <p role="status">{feedback?.type === 'status' ? feedback.message : ''}</p>
                  {feedback?.type === 'alert' && <p role="alert" className="field-error">{feedback.message}</p>}
                </div>
                <button className="button primary" type="submit"
                  disabled={busy !== null || trimmedName === profile.display_name}>
                  {busy === 'name' && <LoadingMark compact />} Сохранить имя
                </button>
              </div>
            </form>
          </section>
          {security && <section className="profile-content load-result" aria-label="Безопасность">
            <PasswordForm email={member.email} onChanged={onPasswordChanged} />
          </section>}
        </div>
      ) : resource.error ? (
        <AsyncState title="Профиль недоступен" description={resource.error} error
          retrying={resource.loading} onRetry={resource.reload} />
      ) : <AsyncState title="Загружаем профиль…" loading />}
    </div>
  )
}

function PasswordForm({ email, onChanged }: { email: string; onChanged: () => void }) {
  const trigger = useRef<HTMLButtonElement>(null)
  const current = useRef<HTMLInputElement>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  function toggle() {
    const show = !open
    setError('')
    setOpen(show)
    window.requestAnimationFrame(() => (show ? current.current : trigger.current)?.focus())
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    const data = new FormData(event.currentTarget)
    const old = String(data.get('current-password'))
    const next = String(data.get('new-password'))
    if (next !== data.get('repeat-password')) {
      setError('Новые пароли не совпадают.')
      event.currentTarget.querySelector<HTMLInputElement>('[name="repeat-password"]')?.focus()
      return
    }
    if (new TextEncoder().encode(next).length > 72 || next === old) {
      setError('Укажите другой пароль. Максимальная длина — 72 байта.')
      return
    }
    setError('')
    setBusy(true)
    try {
      await changePassword(old, next)
      onChanged()
    } catch (failure) {
      const message = failure instanceof ApiError && failure.status === 422
        ? 'Введите текущий пароль и новый пароль от 12 символов.'
        : errorMessage(failure)
      setError(message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="profile-card">
      <div className="profile-password-heading">
        <div>
          <h2>Пароль</h2>
          <p className="muted">После смены пароля нужно войти заново.</p>
        </div>
        <button ref={trigger} className="button secondary" type="button" aria-expanded={open}
          aria-controls="profile-password-form" hidden={open} onClick={toggle}>Сменить пароль</button>
      </div>
      <div id="profile-password-form" hidden={!open}>
        {open && <form className="profile-password-form" onSubmit={submit}>
          <input type="hidden" name="username" autoComplete="username" value={email} />
          <label className="field"><span>Текущий пароль</span>
            <input ref={current} name="current-password" type="password" autoComplete="current-password"
              required maxLength={1024} readOnly={busy} />
          </label>
          <div className="field">
            <label htmlFor="profile-new-password">Новый пароль</label>
            <input id="profile-new-password" name="new-password" type="password" autoComplete="new-password"
              aria-describedby="profile-password-length"
              required minLength={12} maxLength={72} readOnly={busy} />
            <span className="muted" id="profile-password-length">От 12 символов</span>
          </div>
          <label className="field"><span>Повторите новый пароль</span>
            <input name="repeat-password" type="password" autoComplete="new-password"
              required minLength={12} maxLength={72} readOnly={busy} />
          </label>
          {error && <p role="alert" className="field-error">{error}</p>}
          <div className="profile-actions">
            <button className="button primary" type="submit" disabled={busy}>
              {busy && <LoadingMark compact />} Обновить пароль
            </button>
            <button className="button secondary" type="button" disabled={busy} onClick={toggle}>Отмена</button>
          </div>
        </form>}
      </div>
    </div>
  )
}
