import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, errorMessage } from '../../api/client'
import { getReplyDraft, MAX_REQUEST_REPLY_LENGTH, saveReplyDraft, type CloudReplyDraft } from '../../api/requests'
import { useResource } from '../../hooks/useResource'
import type { ReplyDraft } from './replyDrafts'

export function useCloudReplyDraft(
  id: string, enabled: boolean, local: ReplyDraft | undefined,
  onDraft: (id: string, draft: ReplyDraft | null, sent?: ReplyDraft) => void,
) {
  const load = useCallback((signal: AbortSignal) => enabled ? getReplyDraft(id, signal) : Promise.resolve(null), [enabled, id])
  const initial = useResource(load)
  const [synced, setSynced] = useState<{ draft: CloudReplyDraft | null; localBase?: string | null }>()
  const [failure, setFailure] = useState('')
  const [saving, setSaving] = useState(false)
  const working = useRef(false)
  const remote = synced ? synced.draft : initial.data
  const initialError = synced ? '' : initial.error
  const ready = Boolean(synced || (!initial.loading && !initialError))
  const revision = remote?.revision ?? null
  const base = local?.baseRevision ?? null
  const received = Boolean(local?.attemptId && local.attemptId === revision && local.text === remote?.text)
  const conflict = Boolean(local && !received && base !== revision && synced?.localBase !== base)
  const valid = !local || [...local.text].length <= MAX_REQUEST_REPLY_LENGTH

  useEffect(() => {
    if (!enabled || !ready || !local) return
    if (received || (!local.text && revision === null)) {
      onDraft(id, null, local)
      return
    }
    if (conflict || !valid || failure || saving) return
    async function save() {
      if (!local || working.current) return
      working.current = true
      setSaving(true)
      try {
        const draft = await saveReplyDraft(id, local.text, local.attemptId ?? crypto.randomUUID(), revision)
        setSynced({ draft, localBase: base })
        onDraft(id, null, local)
      } catch (error) {
        setFailure(error instanceof ApiError && error.code === 'reply_draft_changed'
          ? 'Черновик изменился на другом устройстве.'
          : errorMessage(error))
      } finally {
        working.current = false
        setSaving(false)
      }
    }
    const timer = window.setTimeout(save, 350)
    function flushOnHide() {
      if (document.visibilityState === 'hidden') void save()
    }
    document.addEventListener('visibilitychange', flushOnHide)
    return () => {
      window.clearTimeout(timer)
      document.removeEventListener('visibilitychange', flushOnHide)
    }
  }, [enabled, ready, local, received, revision, conflict, valid, failure, saving, id, base, onDraft])

  async function retry() {
    if (working.current) return
    working.current = true
    setSaving(true)
    try {
      const draft = await getReplyDraft(id)
      setSynced({ draft, localBase: base })
      setFailure('')
    } catch (error) {
      setFailure(errorMessage(error))
    } finally {
      working.current = false
      setSaving(false)
    }
  }

  const unsynced = Boolean(enabled && (!ready || (local && !received)))
  const error = enabled ? initialError || failure || (conflict ? 'Черновик изменился на другом устройстве.' : '') : ''
  return {
    draft: local ?? (remote ? { text: remote.text, attemptId: remote.revision } : undefined),
    revision, error, unsynced, saving, retry,
    loading: enabled && !synced && initial.loading,
    async clearAfterSend() {
      setSynced({ draft: null })
      try {
        setSynced({ draft: await getReplyDraft(id) })
      } catch {
        // The next open loads the current server version.
      }
    },
  }
}
