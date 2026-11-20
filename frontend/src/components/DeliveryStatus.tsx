import { useRef, useState } from 'react'
import { errorMessage } from '../api/client'
import { retryDelivery, type Delivery } from '../api/delivery'

const labels: Record<Delivery['status'], string> = {
  pending: 'Ожидает отправки',
  delivered: 'Доставка подтверждена',
  failed: 'Доставка завершилась ошибкой',
  unknown: 'Доставка не подтверждена',
}

export function DeliveryStatus({ delivery, onRefresh, allowRetry = false }: {
  delivery: Delivery
  onRefresh: () => void
  allowRetry?: boolean
}) {
  const [latest, setLatest] = useState<Delivery | null>(null)
  const [retrying, setRetrying] = useState(false)
  const [error, setError] = useState('')
  const inFlight = useRef(false)
  const status = useRef<HTMLParagraphElement>(null)
  const current = latest ?? delivery

  async function retry(button: HTMLButtonElement) {
    if (inFlight.current) return
    inFlight.current = true
    setRetrying(true)
    setError('')
    try {
      const result = await retryDelivery(current.request_id, current.event_id)
      if (!result.can_retry && document.activeElement === button) status.current?.focus()
      setLatest(result)
      if (result.status === 'unknown') setError('Квитанция пока не получена. Проверьте Test URL в n8n.')
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      inFlight.current = false
      setRetrying(false)
    }
  }

  return (
    <section className="delivery-status" aria-label="Доставка в n8n">
      <h2>Доставка в n8n</h2>
      <p ref={status} role="status" tabIndex={-1}>{retrying ? 'Повторяем доставку…' : labels[current.status]}</p>
      <dl>
        <div><dt>ID события</dt><dd><code translate="no">{current.event_id}</code></dd></div>
        {current.receipt && <div><dt>Выполнение n8n</dt><dd><code translate="no">{current.receipt.execution_id}</code></dd></div>}
      </dl>
      {error && <p className="field-error" role="alert">{error}</p>}
      {current.status === 'unknown' && !current.can_retry && <p className="muted">Сверьте выполнение в n8n по ID события.</p>}
      {current.status === 'failed' && <p className="muted">Заявка сохранена. Проверьте журнал выполнения n8n.</p>}
      {(current.status === 'pending' || current.status === 'unknown') && (
        <div className="delivery-actions">
          {allowRetry && current.can_retry && (
            <button type="button" className="button secondary" aria-disabled={retrying} onClick={(event) => void retry(event.currentTarget)}>
              Повторить доставку
            </button>
          )}
          <button type="button" className="text-button" disabled={retrying} onClick={onRefresh}>Обновить статус доставки</button>
        </div>
      )}
    </section>
  )
}
