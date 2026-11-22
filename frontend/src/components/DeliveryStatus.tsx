import { useRef, useState } from 'react'
import { errorMessage } from '../api/client'
import { retryDelivery, type Delivery } from '../api/delivery'
import { LoadingMark } from './LoadingMark'

const labels: Record<Delivery['status'], string> = {
  pending: 'Ожидает отправки',
  delivered: 'Доставка подтверждена',
  failed: 'Доставка завершилась ошибкой',
  unknown: 'Доставка не подтверждена',
}

export function DeliveryStatus({ delivery: current, onRefresh, onRetried, refreshing = false }: {
  delivery: Delivery
  onRefresh: () => void
  onRetried?: (delivery: Delivery) => void
  refreshing?: boolean
}) {
  const [retrying, setRetrying] = useState(false)
  const [error, setError] = useState('')
  const inFlight = useRef(false)
  const status = useRef<HTMLParagraphElement>(null)

  async function retry(button: HTMLButtonElement) {
    if (inFlight.current || refreshing || !onRetried) return
    inFlight.current = true
    setRetrying(true)
    setError('')
    try {
      const result = await retryDelivery(current.request_id, current.event_id)
      if (!result.can_retry && document.activeElement === button) status.current?.focus()
      onRetried(result)
      if (result.status === 'unknown') setError('Квитанция пока не получена. Проверьте Test URL в n8n.')
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      inFlight.current = false
      setRetrying(false)
    }
  }

  return (
    <section className="delivery-status" data-state={current.status} aria-label="Доставка в n8n">
      <h2>Доставка в n8n</h2>
      <p ref={status} role="status" tabIndex={-1}>
        {retrying && <LoadingMark compact />}
        {retrying ? 'Повторяем доставку…' : labels[current.status]}
      </p>
      <time className="thread-time" dateTime={current.updated_at}>{new Date(current.updated_at).toLocaleString('ru-RU')}</time>
      <details className="delivery-details"><summary>Детали доставки</summary><dl>
        <div><dt>ID события</dt><dd><code translate="no">{current.event_id}</code></dd></div>
        {current.receipt && <div><dt>Выполнение n8n</dt><dd><code translate="no">{current.receipt.execution_id}</code></dd></div>}
      </dl></details>
      {error && <p className="field-error" role="alert">{error}</p>}
      {current.status === 'unknown' && !current.can_retry && <p className="muted">Сверьте выполнение в n8n по ID события.</p>}
      {current.status === 'failed' && <p className="muted">Заявка сохранена. Проверьте журнал выполнения n8n.</p>}
      {(current.status === 'pending' || current.status === 'unknown') && (
        <div className="delivery-actions">
          {onRetried && current.can_retry && (
            <button type="button" className="button secondary" aria-disabled={retrying || refreshing} onClick={(event) => void retry(event.currentTarget)}>
              Повторить доставку
            </button>
          )}
          <button type="button" className="text-button" disabled={retrying || refreshing} onClick={onRefresh}>Обновить статус доставки</button>
        </div>
      )}
    </section>
  )
}
