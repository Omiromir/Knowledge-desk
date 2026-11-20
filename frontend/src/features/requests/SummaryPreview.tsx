import { useRef, useState } from 'react'
import { ApiError, errorMessage } from '../../api/client'
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
  const label = pending
    ? 'Готовим…'
    : summary
      ? 'Резюме готово'
      : failure && !failure.blocked
        ? 'Повторить'
        : 'Подготовить резюме'

  async function prepare() {
    if (disabled || locked || inFlight.current) return
    inFlight.current = true
    setPending(true)
    setPreview(null)
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
          className="button secondary summary-action"
          type="button"
          disabled={disabled}
          aria-disabled={locked || undefined}
          onClick={() => void prepare()}
          aria-describedby="summary-hint"
        >
          {pending && <span className="summary-spinner" aria-hidden="true" />}
          {label}
        </button>
      </div>
      <p id="summary-hint" className="summary-hint">
        {tooLong
          ? `Сократите вопрос до ${MAX_SUMMARY_INPUT} символов для резюме.`
          : 'Модели передаётся только текст вопроса.'}
      </p>
      <div className="summary-feedback" role="status" aria-atomic="true">
        {pending && (
          <div className="summary-output summary-loading">
            <p className="summary-caption">Готовим резюме…</p>
            <div className="summary-skeleton" aria-hidden="true">
              <span />
              <span />
              <span />
            </div>
          </div>
        )}
        {failure && <p className="summary-error">{failure.message}</p>}
        {summary && (
          <div className="summary-output">
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
