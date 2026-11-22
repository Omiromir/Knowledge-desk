import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from 'react'
import { errorMessage } from '../../api/client'
import { deleteDocument, updateDocument } from '../../api/documents'
import type { DocumentRecord } from '../../api/types'
import { LoadingMark } from '../../components/LoadingMark'

export function DocumentActions({
  document,
  onSaved,
  onDeleted,
}: {
  document: DocumentRecord
  onSaved: (record: DocumentRecord) => void
  onDeleted: () => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const running = useRef(false)
  const [mode, setMode] = useState<'edit' | 'delete' | null>(null)
  const [fields, setFields] = useState(() => ({
    title: document.title,
    source: document.source,
    text: document.text,
  }))
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const editing = mode === 'edit'
  const dirty = editing && (
    fields.title !== document.title || fields.source !== document.source || fields.text !== document.text
  )

  useLayoutEffect(() => {
    if (mode && !dialog.current?.open) dialog.current?.showModal()
  }, [mode])

  useEffect(() => {
    if (!dirty && !pending) return
    function preventExit(event: BeforeUnloadEvent) {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', preventExit)
    return () => window.removeEventListener('beforeunload', preventExit)
  }, [dirty, pending])

  function open(action: 'edit' | 'delete') {
    setMode(action)
    setError('')
    setFields({ title: document.title, source: document.source, text: document.text })
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (running.current) return
    if (editing && Object.values(fields).some((value) => !value.trim())) {
      setError('Заполните название, источник и текст.')
      return
    }
    running.current = true
    setPending(true)
    setError('')
    try {
      if (editing) {
        const saved = await updateDocument(document.id, fields)
        dialog.current?.close()
        onSaved(saved)
      } else {
        await deleteDocument(document.id)
        dialog.current?.close()
        onDeleted()
      }
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      running.current = false
      setPending(false)
    }
  }

  return (
    <div className="document-actions">
      <button className="text-button" type="button" onClick={() => open('edit')}>Редактировать</button>
      <button className="text-button document-danger" type="button" onClick={() => open('delete')}>Удалить</button>
      <dialog
        ref={dialog}
        className="document-dialog"
        aria-labelledby="document-dialog-title"
        aria-describedby={editing ? undefined : 'document-delete-description'}
        onCancel={(event) => {
          if (running.current) event.preventDefault()
        }}
        onClose={() => setMode(null)}
      >
        <form onSubmit={submit} autoComplete="off">
          <h2 id="document-dialog-title">{editing ? 'Редактировать документ' : 'Удалить документ?'}</h2>
          {editing ? (
            <>
              <label className="field">
                Название
                <input
                  name="title"
                  value={fields.title}
                  required
                  disabled={pending}
                  onChange={(event) => setFields({ ...fields, title: event.target.value })}
                />
              </label>
              <label className="field">
                Источник
                <input
                  name="source"
                  value={fields.source}
                  required
                  disabled={pending}
                  onChange={(event) => setFields({ ...fields, source: event.target.value })}
                />
              </label>
              <label className="field">
                Текст документа
                <textarea
                  name="text"
                  rows={9}
                  value={fields.text}
                  required
                  disabled={pending}
                  onChange={(event) => setFields({ ...fields, text: event.target.value })}
                />
              </label>
            </>
          ) : (
            <p id="document-delete-description" className="document-delete-description">
              «{document.title}» исчезнет из каталога и ответов. Удаление нельзя отменить.
            </p>
          )}
          <div role="status" aria-atomic="true" className="document-dialog-status">
            {error && <p className="field-error">{error}</p>}
            {pending && <p>{editing ? 'Сохраняем изменения…' : 'Удаляем документ…'}</p>}
          </div>
          <div className="document-dialog-actions">
            <button type="button" className="button secondary" disabled={pending} onClick={() => dialog.current?.close()}>Отмена</button>
            <button type="submit" className={editing ? 'button primary' : 'button secondary document-danger'} disabled={pending}>
              {pending && <LoadingMark compact />}
              {editing ? 'Сохранить изменения' : 'Удалить документ'}
            </button>
          </div>
        </form>
      </dialog>
    </div>
  )
}
