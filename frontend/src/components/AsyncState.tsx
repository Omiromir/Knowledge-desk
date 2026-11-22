import type { ReactNode } from 'react'
import { LoadingMark } from './LoadingMark'

interface Props {
  title: string
  description?: string
  loading?: boolean
  error?: boolean
  inline?: boolean
  onRetry?: () => void
  retrying?: boolean
  children?: ReactNode
}

export function AsyncState({
  title,
  description,
  loading,
  error,
  inline,
  onRetry,
  retrying = false,
  children,
}: Props) {
  const role = error ? 'alert' : 'status'
  return (
    <div className={`async-state${error ? ' is-error' : ''}${inline ? ' is-inline' : ''}${loading ? ' is-loading' : ' load-result'}`}>
      <div key={role} role={role}>
        {loading && <LoadingMark />}
        <p className="state-title">{title}</p>
        {description ? <p className="muted">{description}</p> : null}
      </div>
      {onRetry ? (
        <button className="button secondary async-retry" type="button"
          aria-disabled={retrying || undefined} aria-busy={retrying || undefined}
          onClick={() => { if (!retrying) onRetry() }}>
          {retrying && <LoadingMark compact />}
          Повторить
        </button>
      ) : null}
      {onRetry && <span className="refresh-status" role="status">{retrying ? 'Повторяем запрос…' : ''}</span>}
      {children}
    </div>
  )
}

