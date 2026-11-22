import { useEffect, useRef, useState, type FormEvent } from 'react'
import { errorMessage } from '../../api/client'
import { consultation, consultationHref, type Consultation, type ConsultationInput } from '../../api/consultations'
import { AsyncState } from '../../components/AsyncState'
import { DeliveryStatus } from '../../components/DeliveryStatus'
import { Icon } from '../../components/Icons'
import { LoadingMark } from '../../components/LoadingMark'
import { readRequestIds, saveRequestIds } from '../requests/history'

const stopMessages: Record<string, string> = {
  step_limit: 'Достигнут лимит в 8 переходов. Начните новую попытку с сохранёнными полями.',
  tool_limit: 'Достигнут лимит в 3 вызова инструмента. Начните новую попытку с сохранёнными полями.',
  tool_timeout: 'Инструмент не ответил вовремя. Можно начать новую попытку.',
  tool_failed: 'Инструмент временно недоступен. Можно начать новую попытку.',
  invalid_tool_output: 'Результат инструмента не прошёл проверку. Подготовьте новую версию.',
  active_timeout: 'Истекло время активной подготовки. Можно начать новую попытку.',
  checkpoint_unavailable: 'Точка подтверждения недоступна. Подготовьте новую версию.',
  preparation_interrupted: 'Сервер перезапустился во время подготовки. Начните новую попытку.',
  storage_failed: 'Не удалось записать заявку. Можно начать новую попытку.',
  draft_changed_by_tool: 'Инструмент изменил поля черновика. Проверьте данные и начните новую попытку.',
  invalid_confirmation: 'Не удалось проверить подтверждение. Подготовьте новую версию.',
}

function Fields({ value, onChange, readOnly }: {
  value: ConsultationInput; onChange: (value: ConsultationInput) => void; readOnly: boolean
}) {
  return (
    <>
      <div className="field">
        <label htmlFor="consultation-name">Имя</label>
        <input id="consultation-name" name="name" autoComplete="name" maxLength={120} required
          value={value.name} readOnly={readOnly} onChange={(event) => onChange({ ...value, name: event.target.value })} />
      </div>
      <div className="field">
        <label htmlFor="consultation-question">Вопрос для консультации</label>
        <textarea id="consultation-question" name="question" rows={4} maxLength={4000} required autoComplete="off"
          value={value.question} readOnly={readOnly} onChange={(event) => onChange({ ...value, question: event.target.value })} />
      </div>
    </>
  )
}

function NewConsultation() {
  const [fields, setFields] = useState<ConsultationInput>({ name: '', question: '' })
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const running = useRef(false)

  async function build(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (running.current || !fields.name.trim() || !fields.question.trim()) return
    running.current = true
    setPending(true)
    setError('')
    try {
      const draft = await consultation('', 'POST', fields)
      window.location.hash = consultationHref(draft.draft_id)
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      running.current = false
      setPending(false)
    }
  }

  return (
    <form className="ask-form" onSubmit={build}>
      <Fields value={fields} onChange={setFields} readOnly={pending} />
      <button className="button consultation-primary" type="submit" disabled={pending || !fields.name.trim() || !fields.question.trim()}>
        {pending && <LoadingMark compact />}
        {pending ? 'Сохраняем…' : 'Подготовить черновик'}
      </button>
      {error && <p className="field-error" role="alert">{error}</p>}
    </form>
  )
}

function ReviewConsultation({ id, onNewConversation }: { id: string; onNewConversation: () => void }) {
  const endpoint = `/${encodeURIComponent(id)}`
  const [draft, setDraft] = useState<Consultation | null>(null)
  const [fields, setFields] = useState<ConsultationInput>({ name: '', question: '' })
  const [pending, setPending] = useState<'load' | 'prepare' | 'save' | 'confirm' | 'cancel' | null>('load')
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const operation = useRef(0)
  const currentAction = useRef<typeof pending>('load')
  const dirty = Boolean(draft && (fields.name !== draft.name || fields.question !== draft.question))
  const valid = Boolean(fields.name.trim() && fields.question.trim())

  function apply(value: Consultation) {
    setDraft(value)
    setFields({ name: value.name, question: value.question })
    if (value.request_id) saveRequestIds([value.request_id, ...readRequestIds().filter((entry) => entry !== value.request_id)])
  }

  useEffect(() => {
    const controller = new AbortController()
    const current = ++operation.current
    const currentRun = () => !controller.signal.aborted && current === operation.current

    async function load() {
      try {
        const row = await consultation(endpoint, 'GET', undefined, controller.signal)
        if (!currentRun()) return
        setError('')
        apply(row)
        if (row.status === 'draft') {
          currentAction.current = 'prepare'
          setPending('prepare')
          const result = await consultation(`${endpoint}/prepare`, 'POST', { version: row.version }, controller.signal)
          if (currentRun()) apply(result)
        }
      } catch (failure) {
        if (currentRun()) setError(errorMessage(failure))
      } finally {
        if (currentRun()) {
          currentAction.current = null
          setPending(null)
        }
      }
    }
    void load()
    return () => { controller.abort(); operation.current += 1 }
  }, [endpoint, reload])

  async function act(action: 'save' | 'confirm' | 'cancel') {
    if (!draft || (currentAction.current && !(action === 'cancel' && currentAction.current === 'prepare'))) return
    if (action === 'confirm' && (dirty || draft.status !== 'awaiting_confirmation')) return
    if (action === 'save' && !valid) return
    const current = ++operation.current
    currentAction.current = action
    setPending(action)
    setError('')
    try {
      if (action === 'save') {
        if (draft.status === 'cancelled') {
          const newDraft = await consultation('', 'POST', fields)
          window.location.hash = consultationHref(newDraft.draft_id)
          return
        }
        const revised = await consultation(endpoint, 'PATCH', { ...fields, version: draft.version })
        if (current !== operation.current) return
        apply(revised)
        currentAction.current = 'prepare'
        setPending('prepare')
        const prepared = await consultation(`${endpoint}/prepare`, 'POST', { version: revised.version })
        if (current === operation.current) apply(prepared)
      } else {
        const visibleData = { name: draft.name, question: draft.question, version: draft.version }
        const result = await consultation(`${endpoint}/${action}`, 'POST', action === 'confirm' ? visibleData : { version: draft.version })
        if (current === operation.current) apply(result)
      }
    } catch (failure) {
      if (current === operation.current) setError(errorMessage(failure))
    } finally {
      if (current === operation.current) {
        currentAction.current = null
        setPending(null)
      }
    }
  }

  function refresh() {
    if (currentAction.current) return
    currentAction.current = 'load'
    setPending('load')
    setReload((value) => value + 1)
  }

  if (!draft) return error
    ? <AsyncState title="Черновик недоступен" description={error} error onRetry={refresh} retrying={Boolean(pending)} />
    : <AsyncState title="Загружаем черновик…" loading />

  const awaiting = draft.status === 'awaiting_confirmation'
  const closed = draft.status === 'failed' || draft.status === 'cancelled'
  return (
    <>
      <p className="consultation-state muted load-result" role="status">
        {pending === 'load' && <LoadingMark compact />}{' '}
        {pending === 'load' ? 'Обновляем черновик…' : draft.status === 'draft' ? 'Черновик сохранён' :
          awaiting ? `Версия ${draft.version} ожидает подтверждения` :
          draft.status === 'submitted' ? 'Заявка отправлена' :
          draft.status === 'cancelled' ? 'Подготовка отменена. Черновик сохранён.' : 'Подготовка остановлена. Черновик сохранён.'}
      </p>
      <form className="ask-form load-result" onSubmit={(event) => event.preventDefault()}>
        <Fields value={fields} onChange={setFields} readOnly={Boolean(pending) || draft.status === 'submitted'} />
        {awaiting && dirty && <p className="muted" role="status">Сохраните новую версию перед подтверждением.</p>}
        {draft.status === 'failed' && (
          <p className="field-error" role="alert">{stopMessages[draft.stop_code ?? ''] ?? 'Проверьте подключение и начните новую попытку.'}</p>
        )}
        <div className="consultation-actions">
          {awaiting && <button className="button consultation-primary" type="button" disabled={Boolean(pending) || dirty}
            onClick={() => void act('confirm')}>
            {pending === 'confirm' && <LoadingMark compact />}
            {pending === 'confirm' ? 'Отправляем…' : 'Подтвердить'}
          </button>}
          {(awaiting || draft.status === 'draft') && <button className="button secondary" type="button"
            disabled={Boolean(pending && pending !== 'prepare')} onClick={() => void act('cancel')}>
            {pending === 'cancel' && <LoadingMark compact />}
            {pending === 'cancel' ? 'Отменяем…' : 'Отменить'}
          </button>}
          {(closed || (awaiting && dirty)) && <button className="button secondary" type="button" disabled={Boolean(pending) || !valid}
            onClick={() => void act('save')}>
            {pending === 'save' && <LoadingMark compact />}
            {pending === 'save' ? 'Сохраняем…' : closed ? 'Начать новую попытку' : 'Сохранить новую версию'}
          </button>}
          {draft.status === 'draft' && !pending && <button className="button secondary" type="button" onClick={refresh}>Продолжить подготовку</button>}
          {draft.request_id && <a className="button secondary" href={`#requests?id=${encodeURIComponent(draft.request_id)}`}>Открыть заявку</a>}
          {draft.request_id && <button className="button secondary" type="button" onClick={onNewConversation}>Новый разговор</button>}
        </div>
      </form>
      {error && <div className="consultation-feedback">
        <p className="field-error" role="alert">{error}</p>
        <button className="text-button" type="button" disabled={Boolean(pending)} onClick={refresh}>Обновить состояние</button>
      </div>}
      {pending === 'prepare' && <div className="consultation-feedback">
        <AsyncState title="Ищем источники и готовим черновик…" loading />
      </div>}
      {draft.delivery && <DeliveryStatus delivery={draft.delivery} onRefresh={refresh} refreshing={pending === 'load'} />}
      {draft.sources.length > 0 && <section className="ask-citations tool-result load-result" aria-labelledby="consultation-sources">
        <h2 id="consultation-sources">Найденные источники</h2>
        {draft.sources.map((source, index) => <details key={source.chunk_id} open={index === 0}><summary>{source.title}</summary>
          <blockquote>{source.text}</blockquote>
          <a className="text-button" href={`${consultationHref(id)}&chunk=${encodeURIComponent(source.chunk_id)}`}>Открыть фрагмент</a>
        </details>)}
      </section>}
    </>
  )
}

export function ConsultationWorkspace({ draftId, onNewConversation }: { draftId: string | null; onNewConversation: () => void }) {
  return (
    <>
      <header className="location-bar">
        <Icon name="compose" /><h1>Консультация</h1>
        <a className="text-button ask-mode-link" href="#ask?tools">К помощнику</a>
      </header>
      <div className="ask-body consultation-body" data-review={Boolean(draftId)}>
        <h2>{draftId ? 'Проверка черновика' : 'Новая консультация'}</h2>
        {draftId ? <ReviewConsultation key={draftId} id={draftId} onNewConversation={onNewConversation} /> : <NewConsultation />}
      </div>
    </>
  )
}
