import { useCallback, useMemo, useState, useSyncExternalStore } from 'react'
import { isObject } from '../../api/types'
import { API_BASE_URL } from '../../config'

export type ReplyDraft = { text: string; attemptId?: string; baseRevision?: string | null }

const draftChanged = 'knowledge-desk-reply-draft'

function storageKey(operatorId: string, requestId: string) {
  return `knowledge-desk.reply-draft.v1:${JSON.stringify([API_BASE_URL, operatorId, requestId])}`
}

function decodeDraft(value: string | null): ReplyDraft | undefined {
  if (!value) return
  try {
    const draft: unknown = JSON.parse(value)
    if (isObject(draft) && typeof draft.text === 'string') {
      return {
        text: draft.text,
        attemptId: typeof draft.attemptId === 'string' ? draft.attemptId : undefined,
        baseRevision: typeof draft.baseRevision === 'string' ? draft.baseRevision : null,
      }
    }
  } catch {
    // Ignore a damaged entry.
  }
}

function subscribe(listener: () => void) {
  window.addEventListener('storage', listener)
  window.addEventListener(draftChanged, listener)
  return () => {
    window.removeEventListener('storage', listener)
    window.removeEventListener(draftChanged, listener)
  }
}

function isSameDraft(current: ReplyDraft | null | undefined, sent: ReplyDraft) {
  return current?.attemptId === sent.attemptId && current?.text === sent.text
}

export function useReplyDraft(operatorId: string | undefined, requestId: string | null) {
  const [temporary, setTemporary] = useState(() => new Map<string, ReplyDraft | null>())
  const key = operatorId && requestId ? storageKey(operatorId, requestId) : null
  const raw = useSyncExternalStore(subscribe, () => {
    try {
      return key ? localStorage.getItem(key) : null
    } catch {
      return null
    }
  })
  const hasTemporaryDraft = requestId !== null && temporary.has(requestId)
  const stored = useMemo(() => decodeDraft(raw), [raw])
  const draft = hasTemporaryDraft ? temporary.get(requestId) ?? undefined : stored

  const changeDraft = useCallback((id: string, next: ReplyDraft | null, sent?: ReplyDraft) => {
    if (operatorId) {
      const key = storageKey(operatorId, id)
      try {
        const current = decodeDraft(localStorage.getItem(key))
        if (sent && !isSameDraft(current, sent)) {
          setTemporary(previous => isSameDraft(previous.get(id), sent)
            ? new Map(previous).set(id, null)
            : previous)
          return
        }
        if (next) localStorage.setItem(key, JSON.stringify(next))
        else localStorage.removeItem(key)
        window.dispatchEvent(new Event(draftChanged))
        setTemporary(previous => {
          if (!previous.has(id)) return previous
          const drafts = new Map(previous)
          drafts.delete(id)
          return drafts
        })
        return
      } catch {
        // Keep the input available in this tab when browser storage fails.
      }
    }
    setTemporary(previous => {
      if (sent && !isSameDraft(previous.get(id), sent) && previous.has(id)) return previous
      const drafts = new Map(previous)
      if (next || operatorId) drafts.set(id, next)
      else drafts.delete(id)
      return drafts
    })
  }, [operatorId])

  return { draft, changeDraft, storageError: Boolean(operatorId && hasTemporaryDraft) }
}
