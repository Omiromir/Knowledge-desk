import { useCallback, useEffect, useState, type RefObject } from 'react'
import { documentSharing, getDocument } from '../../api/documents'
import { errorMessage } from '../../api/client'
import type { DocumentRecord } from '../../api/types'
import { AsyncState } from '../../components/AsyncState'
import { Icon } from '../../components/Icons'
import { useResource } from '../../hooks/useResource'
import { DocumentActions } from './DocumentActions'
import type { Member } from '../auth/AuthGate'

const fontSizes = [16, 18, 20, 22, 24]
const fontSizeKey = 'knowledge-desk.reader-font-size'

function readFontSize() {
  try {
    const saved = Number(localStorage.getItem(fontSizeKey))
    return fontSizes.includes(saved) ? saved : 18
  } catch {
    return 18
  }
}

export function DocumentReader({
  id,
  backLink,
  requestView,
  role,
  onChanged,
}: {
  id: string
  backLink: RefObject<HTMLAnchorElement | null>
  requestView: string
  role?: Member['role']
  onChanged: () => void
}) {
  const load = useCallback(
    (signal: AbortSignal) => getDocument(id, signal),
    [id],
  )
  const { data, loading, error, reload } = useResource(load)
  const loadSharing = useCallback((signal: AbortSignal) => role === 'editor'
    ? documentSharing(id, { signal }) : Promise.resolve(null), [id, role])
  const sharing = useResource(loadSharing)
  const [savedSharing, setSavedSharing] = useState<boolean | null>(null)
  const shared = savedSharing ?? sharing.data
  const [changing, setChanging] = useState(false)
  const [sharingError, setSharingError] = useState('')
  const [saved, setSaved] = useState<DocumentRecord | null>(null)
  const document = saved ?? data
  const [fontSize, setFontSize] = useState(readFontSize)
  const [position, setPosition] = useState<number | null>(null)
  const fontIndex = fontSizes.indexOf(fontSize)

  async function changeSharing(shared: boolean) {
    if (changing) return
    setChanging(true)
    setSharingError('')
    try {
      setSavedSharing(await documentSharing(id, { method: 'PATCH', body: JSON.stringify({ shared }) }))
      onChanged()
    } catch (failure) {
      setSharingError(errorMessage(failure))
    } finally {
      setChanging(false)
    }
  }

  useEffect(() => {
    if (!document) return
    let frame = 0
    function measure() {
      const extent =
        window.document.documentElement.scrollHeight - window.innerHeight
      const ratio = Math.min(1, Math.max(0, window.scrollY / extent))
      setPosition(extent > 0 ? Math.round(ratio * 100) : null)
    }
    function schedule() {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(measure)
    }
    schedule()
    window.addEventListener('scroll', schedule, { passive: true })
    window.addEventListener('resize', schedule)
    const observer = new ResizeObserver(schedule)
    observer.observe(window.document.body)
    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      window.removeEventListener('scroll', schedule)
      window.removeEventListener('resize', schedule)
    }
  }, [document])

  function changeSize(nextIndex: number) {
    const size = fontSizes[nextIndex]
    if (size === undefined) return
    setFontSize(size)
    try {
      localStorage.setItem(fontSizeKey, String(size))
    } catch {
      // Reading controls still work when browser storage is unavailable.
    }
  }

  return (
    <div className="reader-panel">
      <div className="reader-toolbar">
        <div className="reader-toolbar-inner">
          <a className="text-button" href="#documents" ref={backLink}>
            <Icon name="back" />К списку
          </a>
          {document ? (
            <span className="reader-location" aria-hidden="true">
              <span>/</span> {document.title}
            </span>
          ) : null}
          {document ? (
            <div
              className="reader-size"
              role="group"
              aria-label="Размер текста"
            >
              <button
                type="button"
                aria-label="Уменьшить текст"
                disabled={fontIndex === 0}
                onClick={() => changeSize(fontIndex - 1)}
              >
                А−
              </button>
              <span aria-live="polite">{fontSize} px</span>
              <button
                type="button"
                aria-label="Увеличить текст"
                disabled={fontIndex === fontSizes.length - 1}
                onClick={() => changeSize(fontIndex + 1)}
              >
                А+
              </button>
            </div>
          ) : null}
        </div>
        {position !== null && document ? (
          <progress
            className="reading-progress"
            value={position}
            max={100}
            aria-label="Позиция в документе"
          />
        ) : null}
      </div>
      {loading ? (
        <AsyncState title="Открываем документ…" loading />
      ) : error ? (
        <AsyncState
          title="Не удалось открыть документ"
          description={error}
          error
          onRetry={reload}
        />
      ) : document ? (
        <article className="document-reader" aria-label="Текст документа">
          <h1>{document.title}</h1>
          <aside className="reader-notes" aria-label="О документе">
            <h2>Свойства</h2>
            <div className="source">
              <span>Источник</span>
              <p>
                <Icon name="link" />
                {document.source}
              </p>
            </div>
            <a
              className="text-button"
              href={`#requests?document=${encodeURIComponent(document.id)}${requestView ? `&${requestView}` : ''}`}
            >
              <Icon name="compose" />
              Задать вопрос по документу
            </a>
            <details className="document-info">
              <summary>Детали</summary>
              <div className="record-id">
                <span>ID</span>
                <code translate="no">{document.id}</code>
              </div>
            </details>
            {(!role || sharing.data !== null) && <DocumentActions document={document} onSaved={(record) => {
              setSaved(record)
              onChanged()
            }} onDeleted={() => {
              onChanged()
              window.location.hash = '#documents?search'
            }} />}
            {shared !== null && <label className="document-sharing">
              <input type="checkbox" checked={shared} disabled={changing}
                onChange={(event) => void changeSharing(event.currentTarget.checked)} />
              Доступен всем участникам
            </label>}
            {(sharingError || sharing.error) && <p role="alert" className="field-error">{sharingError || sharing.error}</p>}
            {saved && <p className="document-save-status" role="status">Изменения сохранены.</p>}
          </aside>
          <div className="document-text" style={{ fontSize }}>
            {document.text}
          </div>
        </article>
      ) : null}
    </div>
  )
}
