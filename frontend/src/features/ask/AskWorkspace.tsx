import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  askQuestion,
  getAskSource,
  MAX_ASK_LENGTH,
  pdfPageUrl,
  pdfPreviewUrl,
  type AskResult,
} from '../../api/ask'
import { errorMessage } from '../../api/client'
import { AsyncState } from '../../components/AsyncState'
import { Icon } from '../../components/Icons'
import { useResource } from '../../hooks/useResource'

const pdfPanelQuery = '(min-width: 1100px)'

type SourceBack = { href: string; label: string }

function SourceView({ chunkId, back }: { chunkId: string; back: SourceBack }) {
  const load = useCallback(
    (signal: AbortSignal) => getAskSource(chunkId, signal),
    [chunkId],
  )
  const { data, loading, error, reload } = useResource(load)
  const excerpt = useRef<HTMLElement>(null)

  useEffect(() => {
    if (data && excerpt.current) {
      excerpt.current.scrollIntoView({ block: 'center' })
      excerpt.current.focus({ preventScroll: true })
    }
  }, [data])

  return (
    <>
      <header className="location-bar">
        <a className="text-button" href={back.href}>
          <Icon name="back" />{back.label}
        </a>
      </header>
      <div className="ask-body">
        {loading && <AsyncState title="Открываем источник…" loading />}
        {error && (
          <AsyncState
            title="Источник недоступен"
            description={error}
            error
            onRetry={reload}
          />
        )}
        {data && (
          <article
            className="ask-source-document"
            aria-label="Документ и использованный фрагмент"
          >
            <h1>{data.title}</h1>
            {(data.source || data.version_date) && (
              <p className="muted">
                {[data.source, data.version_date].filter(Boolean).join(' · ')}
              </p>
            )}
            <p className="muted">Использованный фрагмент выделен в тексте.</p>
            <p className="ask-source-text">
              {data.before}
              <mark ref={excerpt} tabIndex={-1}>
                {data.text}
              </mark>
              {data.after}
            </p>
          </article>
        )}
      </div>
    </>
  )
}

export function AskWorkspace({
  catalogRevision,
  sourceId,
  sourceBack,
  onPrepareRequest,
}: {
  catalogRevision: number
  sourceId: string | null
  sourceBack: SourceBack
  onPrepareRequest: (text: string) => void
}) {
  const [question, setQuestion] = useState('')
  const [pending, setPending] = useState(false)
  const [response, setResponse] = useState<{
    result: AskResult
    revision: number
  } | null>(null)
  const result = response?.revision === catalogRevision ? response.result : null
  const [previewId, setPreviewId] = useState<string | null>(null)
  const previewTrigger = useRef<HTMLElement>(null)
  const preview = result?.citations.find((citation) => citation.chunk_id === previewId)
  const previewUrl = preview?.page_number
    ? pdfPreviewUrl(preview.doc_id, preview.page_number)
    : null
  const [error, setError] = useState('')
  const running = useRef(false)
  const tooLong =
    question.length > MAX_ASK_LENGTH && [...question].length > MAX_ASK_LENGTH
  const disabled = !question.trim() || tooLong

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (disabled || running.current || result) return
    running.current = true
    setPending(true)
    setError('')
    try {
      setResponse({
        result: await askQuestion(question),
        revision: catalogRevision,
      })
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      running.current = false
      setPending(false)
    }
  }

  if (sourceId) return <SourceView key={sourceId} chunkId={sourceId} back={sourceBack} />

  return (
    <div className={previewUrl ? 'ask-layout-split' : undefined}>
      <header className="location-bar">
        <Icon name="search" />
        <h1>Ответы по базе</h1>
        <a className="text-button ask-mode-link" href="#ask?tools">Помощник</a>
      </header>
      <div className="ask-body">
        <form onSubmit={submit} className="ask-form">
          <div className="field">
            <label htmlFor="ask-question">Вопрос к документам</label>
            <textarea
              id="ask-question"
              name="question"
              rows={4}
              value={question}
              readOnly={pending}
              aria-describedby="ask-hint"
              onChange={(event) => {
                setQuestion(event.target.value)
                setResponse(null)
                setPreviewId(null)
                setError('')
              }}
            />
            <p className={tooLong ? 'field-error' : 'muted'} id="ask-hint">
              {tooLong
                ? `Сократите вопрос до ${MAX_ASK_LENGTH} символов.`
                : 'Для ответа модель получит вопрос и подходящие отрывки из базы.'}
            </p>
          </div>
          <button
            className="button primary"
            type="submit"
            disabled={disabled}
            aria-disabled={pending || Boolean(result) || undefined}
          >
            {pending && <span className="loading-mark" aria-hidden="true" />}
            {pending
              ? 'Ищем ответ…'
              : result
                ? 'Поиск завершён'
                : error
                  ? 'Повторить'
                  : 'Найти ответ'}
          </button>
        </form>
        <div className="ask-feedback" role="status" aria-atomic="true">
          {pending && (
            <p className="muted">Ищем подходящие фрагменты и готовим ответ…</p>
          )}
          {error && <p className="field-error">{error}</p>}
          {result && (
            <section aria-labelledby="ask-result-title">
              <h2 id="ask-result-title">
                {result.answerable ? 'Ответ' : 'В базе недостаточно данных'}
              </h2>
              <p className="ask-answer">{result.answer}</p>
            </section>
          )}
        </div>
        {result?.answerable && (
          <section
            className="ask-citations"
            aria-labelledby="ask-citations-title"
          >
            <h2 id="ask-citations-title">Источники</h2>
            {result.citations.map((citation) => (
              <details
                key={citation.chunk_id}
                data-previewing={citation.chunk_id === previewId || undefined}
              >
                <summary
                  onClick={(event) => {
                    if (!citation.page_number || !window.matchMedia(pdfPanelQuery).matches) return
                    if (previewId === citation.chunk_id) return
                    const details = event.currentTarget.closest('details')
                    if (details) {
                      event.preventDefault()
                      details.open = true
                    }
                    previewTrigger.current = event.currentTarget
                    setPreviewId(citation.chunk_id)
                  }}
                >
                  <Icon name="file" />
                  <span>
                    {citation.title}
                    {citation.page_number && <span className="muted"> · стр. {citation.page_number}</span>}
                  </span>
                </summary>
                {citation.version_date && (
                  <p className="muted">Версия {citation.version_date}</p>
                )}
                <blockquote>{citation.text}</blockquote>
                {citation.page_number ? (
                  <a
                    className="text-button"
                    href={pdfPageUrl(citation.doc_id, citation.page_number)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <Icon name="link" />
                    <span className="ask-pdf-tab-label">В новой вкладке</span>
                    <span className="ask-pdf-link-label">Открыть PDF на стр. {citation.page_number}</span>
                  </a>
                ) : (
                  <a
                    className="text-button"
                    href={`#ask?chunk=${encodeURIComponent(citation.chunk_id)}`}
                  >
                    <Icon name="link" />
                    Открыть фрагмент в документе
                  </a>
                )}
              </details>
            ))}
          </section>
        )}
        {result && !result.answerable && (
          <button
            className="button secondary"
            type="button"
            onClick={() => onPrepareRequest(question.trim())}
          >
            Подготовить заявку
          </button>
        )}
      </div>
      {preview && previewUrl && (
        <aside className="ask-pdf-panel" aria-label="Оригинал PDF">
          <header className="ask-pdf-heading">
            <h2 aria-live="polite">
              <span title={preview.title}>{preview.title}</span>
              <span className="ask-pdf-page"> · стр. {preview.page_number}</span>
            </h2>
            <button
              className="text-button"
              type="button"
              aria-label="Закрыть просмотр PDF"
              onClick={() => {
                setPreviewId(null)
                previewTrigger.current?.focus()
              }}
            >
              Закрыть
            </button>
          </header>
          <iframe
            key={previewUrl}
            src={previewUrl}
            title={`Оригинал PDF — ${preview.title}, страница ${preview.page_number}`}
            referrerPolicy="no-referrer"
          />
        </aside>
      )}
    </div>
  )
}
