import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import { errorMessage } from '../../api/client'
import { uploadDocument } from '../../api/documents'
import type { DocumentRecord } from '../../api/types'
import { AsyncState } from '../../components/AsyncState'
import { Icon } from '../../components/Icons'
import { LoadingMark } from '../../components/LoadingMark'
import { DocumentGraphView } from './DocumentGraphView'

function highlight(text: string, pattern: RegExp | null) {
  const match = pattern?.exec(text)
  if (!match) return text

  return (
    <>
      {text.slice(0, match.index)}
      <mark>{match[0]}</mark>
      {text.slice(match.index + match[0].length)}
    </>
  )
}

export function DocumentBrowser({
  resource: { data, loading, refreshing, error, reload },
  hidden,
  graphView,
  requestComposeHref,
  onUploaded,
  canUpload,
}: {
  resource: {
    data: DocumentRecord[] | null
    loading: boolean
    refreshing: boolean
    error: string
    reload: () => void
  }
  hidden: boolean
  graphView: boolean
  requestComposeHref: string
  onUploaded: () => void
  canUpload: boolean
}) {
  const [query, setQuery] = useState('')
  const list = useRef<HTMLDivElement>(null)
  const picker = useRef<HTMLInputElement>(null)
  const uploading = useRef(false)
  const [upload, setUpload] = useState<
    | { state: 'pending'; name: string }
    | { state: 'ready'; document: DocumentRecord }
    | { state: 'error'; name: string; message: string }
    | null
  >(null)
  const uploaded = upload?.state === 'ready'
    ? (data ? data.find((record) => record.id === upload.document.id) : upload.document)
    : undefined
  const returnTo = useRef<{
    link: HTMLAnchorElement | SVGAElement
    listTop: number
    pageTop: number
  }>(null)
  const term = query.trim()
  const pattern = useMemo(() => term
    ? new RegExp(term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'iu')
    : null, [term])
  const results = useMemo(() => (data ?? []).flatMap((document) => {
    const match = pattern?.exec(document.text)
    if (pattern && !match && !pattern.test(document.title)) return []
    const start = match ? Math.max(0, match.index - 32) : 0
    const end = match ? match.index + match[0].length + 100 : 140
    const preview = document.text.slice(start, end).trim()
    return [
      {
        document,
        preview: `${start ? '…' : ''}${preview}${end < document.text.length ? '…' : ''}`,
      },
    ]
  }), [data, pattern])
  const graphMatches = useMemo(() => graphView && term
    ? new Set(results.map(({ document }) => document.id))
    : null, [graphView, results, term])
  const documentHref = (id: string) => `#documents?id=${encodeURIComponent(id)}${graphView ? '&view=graph' : ''}`

  useLayoutEffect(() => {
    if (hidden || !list.current) return
    const previous = returnTo.current
    returnTo.current = null
    window.scrollTo(0, previous?.pageTop ?? 0)
    if (!previous) return
    list.current.scrollTop = previous.listTop
    if (previous.link.isConnected) previous.link.focus({ preventScroll: true })
  }, [hidden])

  function rememberPosition(link: HTMLAnchorElement | SVGAElement) {
    returnTo.current = {
      link,
      listTop: list.current?.scrollTop ?? 0,
      pageTop: window.scrollY,
    }
  }

  async function addFile(file: File) {
    if (uploading.current) return
    uploading.current = true
    setUpload({ state: 'pending', name: file.name })
    try {
      const document = await uploadDocument(file)
      setUpload({ state: 'ready', document })
      setQuery('')
      if (list.current) list.current.scrollTop = 0
      reload()
      onUploaded()
    } catch (error) {
      setUpload({ state: 'error', name: file.name, message: errorMessage(error) })
    } finally {
      uploading.current = false
    }
  }

  return (
    <section
      className="document-browser"
      aria-label="Библиотека документов"
      hidden={hidden}
    >
      <header className="location-bar">
        <Icon name="file" />
        <h1>Документы</h1>
        <input
          type="file"
          ref={picker}
          hidden
          accept=".pdf,.docx,.txt,.md,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/markdown"
          onChange={(event) => {
            const file = event.currentTarget.files?.[0]
            event.currentTarget.value = ''
            if (file) void addFile(file)
          }}
        />
        {canUpload && <button
          className="button secondary document-upload"
          type="button"
          title="PDF и Word (.docx) до 10 МиБ, TXT и Markdown до 64 КиБ"
          aria-disabled={upload?.state === 'pending' || undefined}
          onClick={() => {
            if (!uploading.current) picker.current?.click()
          }}
        >
          {upload?.state === 'pending' ? <LoadingMark compact /> : <Icon name="upload" />}
          {upload?.state === 'pending' ? 'Обрабатываем…' : 'Загрузить файл'}
        </button>}
        <a
          className="icon-button compose-button mobile-compose"
          href={requestComposeHref}
          aria-label="Новая заявка"
        >
          <Icon name="compose" />
        </a>
      </header>
      <div className="document-upload-status" role="status" aria-atomic="true">
        {upload?.state === 'pending' && (
          <p>
            Добавляем {upload.name} в каталог и поиск…
            {/\.pdf$/i.test(upload.name) && ' Распознавание скана может занять до двух минут.'}
          </p>
        )}
        {upload?.state === 'error' && (
          <p className="field-error"><strong>{upload.name}</strong> · {upload.message}</p>
        )}
        {uploaded && (
          <p>
            <a href={documentHref(uploaded.id)}>
              {uploaded.title}
            </a>{' '}
            доступен в каталоге и ответах.
          </p>
        )}
      </div>
      <header className="panel-heading">
        <label className="search-field">
          <Icon name="search" />
          <input
            id="document-search"
            className="document-search"
            type="search"
            name="document-search"
            aria-label="Поиск по названию и тексту"
            placeholder="Поиск по документам…"
            autoComplete="off"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value)
              if (list.current) list.current.scrollTop = 0
            }}
          />
        </label>
        <div className="document-view-controls">
          <div className="document-view-switch" role="group" aria-label="Вид документов">
            <a className="text-button" href="#documents" aria-current={!graphView || undefined}>
              <Icon name="list" />Список
            </a>
            <a className="text-button" href="#documents?view=graph" aria-current={graphView || undefined}>
              <Icon name="graph" />Граф
            </a>
          </div>
          <button
            type="button"
            className="icon-button outline-button"
            aria-label="Обновить список"
            title="Обновить список"
            onClick={reload}
            disabled={loading || refreshing}
          >
            {refreshing && !error ? <LoadingMark compact /> : <Icon name="reload" />}
          </button>
        </div>
        <span className="refresh-status" role="status">{refreshing && !error ? 'Обновляем документы…' : ''}</span>
      </header>
      <div className="document-results" ref={list}>
        {loading && !error ? (
          <AsyncState title="Загружаем документы…" loading />
        ) : error && !data ? (
          <AsyncState
            title="Не удалось загрузить документы"
            description={error}
            error
            onRetry={reload}
            retrying={loading || refreshing}
          />
        ) : !data?.length ? (
          <AsyncState
            title="Документов пока нет"
            description={canUpload ? 'Загрузите PDF, Word (.docx), TXT или Markdown.' : 'Здесь появятся документы, которыми с вами поделились.'}
          />
        ) : results.length === 0 ? (
          <AsyncState title="Ничего не найдено">
            <button type="button" className="button secondary" onClick={() => {
              setQuery('')
              window.document.getElementById('document-search')?.focus()
            }}>Очистить поиск</button>
          </AsyncState>
        ) : graphView ? (
          <DocumentGraphView documents={data} matching={graphMatches} hrefFor={documentHref} onOpen={rememberPosition} />
        ) : (
          <nav className="document-list load-result" aria-label="Выбор документа">
            <div className="list-heading">
              <h2>{term ? 'Результаты поиска' : 'Все документы'}</h2>
              <span className="item-count">{results.length}</span>
              <span className="source-column-title" aria-hidden="true">Источник</span>
            </div>
            <ul>
              {results.map(({ document, preview }) => (
                <li key={document.id}>
                  <a
                    className="document-item"
                    aria-label={document.title}
                    aria-describedby={`source-${document.id}`}
                    href={documentHref(document.id)}
                    onClick={(event) => rememberPosition(event.currentTarget)}
                  >
                    <span className="document-glyph"><Icon name="file" /></span>
                    <span className="document-summary">
                      <strong>{highlight(document.title, pattern)}</strong>
                      <span className="document-preview">
                        {highlight(preview, pattern)}
                      </span>
                    </span>
                    <span
                      className="document-source"
                      id={`source-${document.id}`}
                    >
                      {document.source}
                    </span>
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        )}
      </div>
      {error && data && <AsyncState title="Не удалось обновить документы" description={error} error inline onRetry={reload} retrying={loading || refreshing} />}
    </section>
  )
}
