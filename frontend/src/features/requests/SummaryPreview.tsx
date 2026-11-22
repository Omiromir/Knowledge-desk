import { useRef, useState } from 'react'
import { ApiError, errorMessage } from '../../api/client'
import { AsyncState } from '../../components/AsyncState'
import { LoadingMark } from '../../components/LoadingMark'
import {
  MAX_SUMMARY_INPUT,
  requestTypeLabels,
  summarizeRequest,
  type RequestSummary,
} from '../../api/summary'

type Preview =
  | { state: 'ready'; text: string; summary: RequestSummary }
  | { state: 'error'; text: string; message: string; blocked: boolean }

export function SummaryPreview({
  question,
  submitting,
}: {
  question: string
  submitting: boolean
}) {
  const [pending, setPending] = useState(false)
  const [preview, setPreview] = useState<Preview | null>(null)
  const inFlight = useRef(false)
  const text = question.trim()
  const tooLong =
    question.length > MAX_SUMMARY_INPUT &&
    [...question].length > MAX_SUMMARY_INPUT
  const failure =
    preview?.state === 'error' && (preview.blocked || preview.text === text)
      ? preview
      : null
  const summary =
    preview?.state === 'ready' && preview.text === text ? preview.summary : null
  const disabled = !text || tooLong || submitting
  const locked = pending || Boolean(summary || failure?.blocked)
  const canRetry = Boolean(failure && !failure.blocked)
  const label = pending && !canRetry
    ? 'Готовим…'
    : summary
      ? 'Резюме готово'
      : canRetry
        ? 'Повторить'
        : 'Подготовить резюме'

  async function prepare() {
    if (disabled || locked || inFlight.current) return
    inFlight.current = true
    setPending(true)
    if (!canRetry) setPreview(null)
    try {
      setPreview({
        state: 'ready',
        text,
        summary: await summarizeRequest(text),
      })
    } catch (error) {
      setPreview({
        state: 'error',
        text,
        message: errorMessage(error),
        blocked:
          error instanceof ApiError &&
          (error.code === 'local_limit' || error.code === 'not_configured'),
      })
    } finally {
      inFlight.current = false
      setPending(false)
    }
  }

  return (
    <section className="summary-preview" aria-labelledby="summary-title">
      <div className="summary-header">
        <h3 id="summary-title">Резюме заявки</h3>
        <button
          className={`button secondary summary-action${canRetry ? ' async-retry' : ''}`}
          type="button"
          disabled={disabled}
          aria-disabled={locked || undefined}
          aria-busy={pending || undefined}
          onClick={() => void prepare()}
          aria-describedby="summary-hint"
        >
          {pending && canRetry && <LoadingMark compact />}
          {label}
        </button>
      </div>
      <p id="summary-hint" className="summary-hint">
        {tooLong
          ? `Сократите вопрос до ${MAX_SUMMARY_INPUT} символов для резюме.`
          : 'Модели передаётся только текст вопроса.'}
      </p>
      {pending && !failure && <AsyncState title="Готовим резюме…" loading />}
      {failure && <AsyncState title={failure.message} error />}
      <div className="summary-feedback" role="status" aria-atomic="true">
        {summary && (
          <div className="summary-output load-result">
            <p className="summary-caption">
              Черновик модели. Проверьте перед использованием.
            </p>
            <p className="summary-text">{summary.summary}</p>
            <dl className="summary-properties">
              <dt>Тип запроса</dt>
              <dd>{requestTypeLabels[summary.request_type]}</dd>
              <dt>Участие сотрудника</dt>
              <dd>{summary.needs_human ? 'Требуется' : 'Не требуется'}</dd>
            </dl>
          </div>
        )}
      </div>
    </section>
  )
}
