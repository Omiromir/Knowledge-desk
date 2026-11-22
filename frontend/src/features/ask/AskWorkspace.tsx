import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import {
  askQuestion,
  getAskSource,
  MAX_ASK_LENGTH,
  pdfPageUrl,
  pdfPreviewUrl,
  type AskResult,
} from '../../api/ask'
import { errorMessage } from '../../api/client'
import type { DocumentRecord } from '../../api/types'
import { AsyncState } from '../../components/AsyncState'
import { Icon } from '../../components/Icons'
import { LoadingMark } from '../../components/LoadingMark'
import { useResource } from '../../hooks/useResource'

const pdfPanelQuery = '(min-width: 1100px)'

type SourceBack = { href: string; label: string }

function SourceView({ chunkId, back }: { chunkId: string; back: SourceBack }) {
  const load = useCallback(
    (signal: AbortSignal) => getAskSource(chunkId, signal),
    [chunkId],
  )
  const { data, loading, refreshing, error, reload } = useResource(load, { key: `source:${chunkId}` })
  const excerpt = useRef<HTMLElement>(null)
  const ready = Boolean(data)

  useLayoutEffect(() => {
    if (ready && excerpt.current) {
      excerpt.current.scrollIntoView({ block: 'center' })
      excerpt.current.focus({ preventScroll: true })
    }
  }, [ready])

  return (
    <>
      <header className="location-bar">
        <a className="text-button" href={back.href}>
          <Icon name="back" />{back.label}
        </a>
        <button type="button" className="icon-button" title="Обновить источник" aria-label="Обновить источник"
          onClick={reload} disabled={loading || refreshing}>{refreshing && !error ? <LoadingMark compact /> : <Icon name="reload" />}</button>
        <span className="refresh-status" role="status">{refreshing && !error ? 'Обновляем источник…' : ''}</span>
      </header>
      <div className="ask-body source-body">
        {loading && !error && <AsyncState title="Открываем источник…" loading />}
        {error && (
          <AsyncState
            title="Источник недоступен"
            description={error}
            error
            inline={Boolean(data)}
            onRetry={reload}
            retrying={loading || refreshing}
          />
        )}
        {data && (
          <article
            className="ask-source-document load-result"
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

function PdfFrame({ src, title }: { src: string; title: string }) {
  const [loaded, setLoaded] = useState(false)
  return (
    <div className="ask-pdf-frame">
      {!loaded && <AsyncState title="Открываем PDF…" loading />}
      <iframe src={src} title={title} loading="lazy" onLoad={() => setLoaded(true)}
        className={loaded ? 'load-result' : undefined} aria-hidden={loaded ? undefined : true} tabIndex={loaded ? undefined : -1}
        referrerPolicy="no-referrer" />
    </div>
  )
}

export function AskWorkspace({
  documents,
  catalogRevision,
  sourceId,
  sourceBack,
  onPrepareRequest,
}: {
  documents: DocumentRecord[]
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
  const showQuestion = pending || Boolean(result) || Boolean(error)
  const input = useRef<HTMLTextAreaElement>(null)
  const questionTitle = useRef<HTMLHeadingElement>(null)
  const running = useRef(false)
  const tooLong =
    question.length > MAX_ASK_LENGTH && [...question].length > MAX_ASK_LENGTH
  const disabled = !question.trim() || tooLong

  useLayoutEffect(() => {
    if (showQuestion && questionTitle.current?.getClientRects().length && document.activeElement === document.body) {
      questionTitle.current.focus({ preventScroll: true })
    }
  }, [showQuestion, result])

  async function submit() {
    if (disabled || running.current || result) return
    running.current = true
    setPreviewId(null)
    setPending(true)
    try {
      setResponse({
        result: await askQuestion(question),
        revision: catalogRevision,
      })
      setError('')
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      running.current = false
      setPending(false)
    }
  }

  if (sourceId) return <SourceView key={sourceId} chunkId={sourceId} back={sourceBack} />

  return (
    <div className={previewUrl ? 'ask-desk ask-layout-split' : 'ask-desk'}>
      <header className="location-bar">
        <Icon name="search" />
        <h1>Ответы по базе</h1>
        <a className="text-button ask-mode-link" href="#ask?tools">Помощник</a>
      </header>
      <div className="ask-body">
        {showQuestion ? (
          <div className="ask-question-summary">
            <h2 ref={questionTitle} tabIndex={-1}>{question.trim()}</h2>
            <button type="button" className="text-button" disabled={pending} onClick={() => {
              setResponse(null)
              setPreviewId(null)
              setError('')
              requestAnimationFrame(() => input.current?.focus())
            }}>
              <Icon name="compose" /> {pending && !error ? 'Ищем ответ…' : 'Изменить вопрос'}
            </button>
          </div>
        ) : (
          <form onSubmit={(event) => {
            event.preventDefault()
            void submit()
          }} className="ask-form">
            <span className="surface-symbol" aria-hidden="true"><Icon name="chat" /></span>
            <h2><label htmlFor="ask-question">Спросить по документам</label></h2>
            <div className="ask-composer">
              <textarea
                ref={input}
                id="ask-question"
                name="question"
                autoComplete="off"
                rows={3}
                value={question}
                placeholder="Что вы хотите узнать?"
                aria-invalid={tooLong || undefined}
                aria-describedby={tooLong ? 'ask-limit' : undefined}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.nativeEvent.isComposing) {
                    event.preventDefault()
                    if (!disabled) event.currentTarget.form?.requestSubmit()
                  }
                }}
                onChange={(event) => {
                  setQuestion(event.target.value)
                  setError('')
                }}
              />
              <div className="ask-composer-actions">
                <span className="ask-key-hint" aria-hidden="true">
                  <kbd>{/Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl'}</kbd> + <kbd>Enter</kbd>
                </span>
                <button className="button primary" type="submit" disabled={disabled}
                  aria-keyshortcuts="Control+Enter Meta+Enter">
                  <Icon name="search" />
                  Найти ответ
                </button>
              </div>
            </div>
            {tooLong && <p className="field-error" id="ask-limit" role="status">Сократите вопрос до {MAX_ASK_LENGTH} символов.</p>}
          </form>
        )}
        {!result && !question && documents.length > 0 && (
          <section className="ask-starters" aria-labelledby="ask-start-title">
            <h3 id="ask-start-title">Начать с документа</h3>
            <ul>
              {documents.slice(0, 3).map((doc) => (
                <li key={doc.id}>
                  <button type="button" onClick={() => {
                    setQuestion(`О чём говорится в документе «${doc.title}»?`)
                    setError('')
                    input.current?.focus()
                  }}>
                    <Icon name="file" /><span>{doc.title}</span><Icon name="compose" />
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
        {showQuestion && <div className="ask-result-area">
          {pending && !error && <div className="ask-feedback">
            <AsyncState title="Ищем подходящие фрагменты и готовим ответ…" loading />
          </div>}
          {error && <AsyncState title="Не удалось получить ответ" description={error} error onRetry={() => void submit()} retrying={pending} />}
          {result && <div className="ask-feedback load-result" role="status" aria-atomic="true">
            <section aria-labelledby="ask-result-title">
              <h2 id="ask-result-title">
                {result.answerable ? 'Ответ' : 'В базе недостаточно данных'}
              </h2>
              <p className="ask-answer">{result.answer}</p>
            </section>
          </div>}
          {result?.answerable && (
            <section
              className="ask-citations load-result"
              aria-labelledby="ask-citations-title"
            >
              <h2 id="ask-citations-title">Источники <span>{result.citations.length}</span></h2>
              {result.citations.map((citation, index) => (
                <details
                  key={citation.chunk_id}
                  name="ask-cited-excerpts"
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
                    <span className="citation-index" aria-hidden="true">{index + 1}</span>
                    <span>{citation.title}</span>
                    {citation.page_number && <small>стр. {citation.page_number}</small>}
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
              className="button secondary load-result"
              type="button"
              onClick={() => onPrepareRequest(question.trim())}
            >
              Подготовить заявку
            </button>
          )}
        </div>}
      </div>
      {preview?.page_number && previewUrl && (
        <aside className="ask-pdf-panel" aria-label="Оригинал PDF">
          <header className="ask-pdf-heading">
            <h2 aria-live="polite">
              <span title={preview.title}>{preview.title}</span>
              <span className="ask-pdf-page"> · стр. {preview.page_number}</span>
            </h2>
            <a className="icon-button" href={pdfPageUrl(preview.doc_id, preview.page_number)} target="_blank" rel="noreferrer"
              title="Открыть страницу в новой вкладке" aria-label="Открыть страницу в новой вкладке"><Icon name="external" /></a>
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
          <PdfFrame
            key={previewUrl}
            src={previewUrl}
            title={`Оригинал PDF — ${preview.title}, страница ${preview.page_number}`}
          />
        </aside>
      )}
    </div>
  )
}
