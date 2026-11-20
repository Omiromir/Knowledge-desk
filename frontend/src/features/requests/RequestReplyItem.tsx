import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { errorMessage } from '../../api/client'
import { MAX_REQUEST_REPLY_LENGTH } from '../../api/requests'
import type { RequestReply } from '../../api/types'
import { Icon } from '../../components/Icons'

const replyDate = new Intl.DateTimeFormat('ru-RU', {
  dateStyle: 'medium',
  timeStyle: 'short',
})

export function RequestReplyItem({ reply, busy, onChange, onDeleted }: {
  reply: RequestReply
  busy: boolean
  onChange?: (id: string, text: string | null) => Promise<void>
  onDeleted: () => void
}) {
  const menuId = useId()
  const menu = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const editor = useRef<HTMLTextAreaElement>(null)
  const dialog = useRef<HTMLDialogElement>(null)
  const cancelDelete = useRef<HTMLButtonElement>(null)
  const [draft, setDraft] = useState<string | null>(null)
  const [failure, setFailure] = useState('')
  const text = draft?.trim() ?? ''
  const tooLong = text.length > MAX_REQUEST_REPLY_LENGTH && [...text].length > MAX_REQUEST_REPLY_LENGTH
  const editing = draft !== null

  useEffect(() => {
    if (editing) editor.current?.focus()
  }, [editing])

  function finishEdit() {
    setDraft(null)
    setFailure('')
    requestAnimationFrame(() => trigger.current?.focus())
  }

  function closeDelete() {
    dialog.current?.close()
    trigger.current?.focus()
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!onChange || busy || !text || tooLong || text === reply.text) return
    setFailure('')
    try {
      await onChange(reply.id, text)
      finishEdit()
    } catch (error) {
      setFailure(errorMessage(error))
    }
  }

  async function remove() {
    if (!onChange || busy) return
    setFailure('')
    try {
      await onChange(reply.id, null)
      dialog.current?.close()
      onDeleted()
    } catch (error) {
      setFailure(errorMessage(error))
    }
  }

  return (
    <li className="request-activity-event">
      <span className="request-avatar" aria-hidden="true">О</span>
      <div className="request-reply">
        <div className="request-reply-heading">
          <p className="request-reply-meta">
            <strong>Ответ</strong>
            <time dateTime={reply.created_at}>{replyDate.format(new Date(reply.created_at))}</time>
            {reply.edited_at && <span className="muted">изменено</span>}
          </p>
          {onChange && !editing && (
            <button ref={trigger} className="icon-button reply-menu-trigger" type="button"
              aria-label="Действия с ответом" popoverTarget={menuId} disabled={busy}>
              <Icon name="more" />
            </button>
          )}
          <div id={menuId} ref={menu} className="reply-menu" popover="auto">
            <button className="text-button" type="button" onClick={() => {
              menu.current?.hidePopover()
              setFailure('')
              setDraft(reply.text)
            }}>Редактировать</button>
            <button className="text-button reply-danger" type="button" onClick={() => {
              menu.current?.hidePopover()
              setFailure('')
              dialog.current?.showModal()
              cancelDelete.current?.focus()
            }}>Удалить</button>
          </div>
        </div>
        {editing ? (
          <form className="reply-edit" onSubmit={save}>
            <textarea ref={editor} name="reply" aria-label="Редактировать ответ" rows={3}
              aria-describedby={tooLong || failure ? `${menuId}-edit-error` : undefined}
              aria-invalid={tooLong || undefined} autoComplete="off" readOnly={busy}
              value={draft} onChange={(event) => setDraft(event.target.value)} />
            {(tooLong || failure) && (
              <p id={`${menuId}-edit-error`} className="field-error" role="alert">
                {tooLong ? 'Сократите ответ до 10 000 символов.' : failure}
              </p>
            )}
            <div className="request-composer-actions">
              <button className="text-button" type="button" disabled={busy} onClick={finishEdit}>Отмена</button>
              <button className="button primary" type="submit"
                disabled={busy || !text || tooLong || text === reply.text}>Сохранить</button>
            </div>
          </form>
        ) : <p className="request-reply-text">{reply.text}</p>}
        <dialog ref={dialog} className="document-dialog reply-delete-dialog" aria-labelledby={`${menuId}-delete-title`}
          onCancel={(event) => {
            event.preventDefault()
            if (!busy) closeDelete()
          }}>
          <h2 id={`${menuId}-delete-title`}>Удалить ответ?</h2>
          <p className="document-delete-description">Ответ исчезнет из истории заявки.</p>
          {failure && <p className="field-error" role="alert">{failure}</p>}
          <div className="document-dialog-actions">
            <button ref={cancelDelete} className="button secondary" type="button" disabled={busy}
              onClick={closeDelete}>Отмена</button>
            <button className="button secondary reply-danger" type="button" disabled={busy}
              onClick={() => void remove()}>Удалить ответ</button>
          </div>
        </dialog>
      </div>
    </li>
  )
}
