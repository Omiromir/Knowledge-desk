interface Props {
  title: string
  description?: string
  loading?: boolean
  error?: boolean
  onRetry?: () => void
}

export function AsyncState({
  title,
  description,
  loading,
  error,
  onRetry,
}: Props) {
  return (
    <div className={`async-state${error ? ' is-error' : ''}`}>
      <div role={error ? 'alert' : 'status'}>
        {loading ? <span className="loading-mark" aria-hidden="true" /> : null}
        <p className="state-title">{title}</p>
        {description ? <p className="muted">{description}</p> : null}
      </div>
      {onRetry ? (
        <button className="button secondary" onClick={onRetry}>
          Повторить загрузку
        </button>
      ) : null}
    </div>
  )
}
