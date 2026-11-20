import { useRef, useState, type FormEvent } from 'react'
import { errorMessage } from '../../api/client'
import { MAX_TOOL_MESSAGE_LENGTH, runAssistant, type ToolTurn } from '../../api/tools'
import { AsyncState } from '../../components/AsyncState'

export function ToolWorkspace({ onNewConversation }: { onNewConversation: () => void }) {
  const [message, setMessage] = useState('')
  const [pending, setPending] = useState(false)
  const [feedback, setFeedback] = useState<ToolTurn | null>(null)
  const [error, setError] = useState('')
  const running = useRef(false)
  const execution = feedback?.execution
  const failure = execution?.result.status === 'error' ? execution.result : null
  const failureMessage = failure?.reason || error
  const tooLong = [...message.trim()].length > MAX_TOOL_MESSAGE_LENGTH
  const runAgain = Boolean(error || failure?.retryable)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!message.trim() || tooLong || running.current) return
    running.current = true
    setPending(true)
    setFeedback(null)
    setError('')
    try {
      setFeedback(await runAssistant(message))
    } catch (problem) {
      setError(errorMessage(problem))
    } finally {
      running.current = false
      setPending(false)
    }
  }

  return (
    <>
      <header className="location-bar">
        <h1>Помощник</h1>
        <a className="text-button ask-mode-link" href="#ask">К ответам по базе</a>
      </header>
      <div className="ask-body tool-workspace">
        <div className="conversation-actions">
          <a className="text-button" href="#ask?consultation">Заявка на консультацию</a>
          <button className="text-button" type="button" onClick={onNewConversation}>Новый разговор</button>
        </div>
        <form onSubmit={submit} className="ask-form">
          <div className="field">
            <label htmlFor="tool-message">Что сделать?</label>
            <textarea
              id="tool-message"
              name="message"
              rows={4}
              autoComplete="off"
              value={message}
              readOnly={pending}
              aria-invalid={tooLong || undefined}
              aria-describedby="tool-hint"
              onChange={(event) => {
                setMessage(event.target.value)
                setFeedback(null)
                setError('')
              }}
            />
            <p className={tooLong ? 'field-error' : 'muted'} id="tool-hint">
              {tooLong
                ? `Сократите сообщение до ${MAX_TOOL_MESSAGE_LENGTH} символов.`
                : 'Попросите найти фрагменты или подготовить черновик с именем и вопросом.'}
            </p>
          </div>
          <button className="button secondary tool-submit" type="submit" disabled={pending || !message.trim() || tooLong}>
            {pending && <span className="loading-mark" aria-hidden="true" />}
            {pending ? 'Выполняется…' : runAgain ? 'Повторить запрос' : 'Запустить помощника'}
          </button>
        </form>

        <div className="tool-feedback">
          {pending && (
            <>
              <AsyncState title="Выбираем инструмент и ждём результат…" />
              <div className="tool-skeleton" aria-hidden="true"><span /><span /></div>
            </>
          )}
          {feedback?.message && <p className="muted" role="status">{feedback.message}</p>}
          {execution && (
            <p className="tool-call" role="status">
              {execution.tool ? <code translate="no">{execution.tool}</code> : 'Неизвестный инструмент'}
              {execution.result.attempts === 2 && <span className="muted">Повторён 1 раз</span>}
            </p>
          )}
          {failureMessage && <p className="field-error" role="alert">{failureMessage}</p>}
          {execution?.tool === 'search_documents' && execution.result.status === 'success' && (
            <section className="tool-result ask-citations" aria-labelledby="tool-search-title">
              <h2 id="tool-search-title">Найденные фрагменты ({execution.result.data.fragments.length})</h2>
              {execution.result.data.fragments.length === 0 && <p className="muted">Подходящих фрагментов нет. Уточните запрос.</p>}
              {execution.result.data.fragments.map((fragment) => (
                <details key={fragment.chunk_id}>
                  <summary>{fragment.title}</summary>
                  <blockquote>{fragment.text}</blockquote>
                  <a className="text-button" href={`#ask?tools&chunk=${encodeURIComponent(fragment.chunk_id)}`}>
                    Открыть фрагмент <code translate="no">{fragment.chunk_id}</code>
                  </a>
                </details>
              ))}
            </section>
          )}
          {execution?.tool === 'prepare_request' && execution.result.status === 'success' && (
            <section className="tool-result" aria-labelledby="tool-draft-title">
              <h2 id="tool-draft-title">Черновик заявки</h2>
              <dl className="tool-draft">
                <dt>Имя</dt><dd>{execution.result.data.name}</dd>
                <dt>Вопрос</dt><dd>{execution.result.data.question}</dd>
              </dl>
              <p className="muted">Черновик показан для проверки. Отправка доступна в разделе «Заявки».</p>
            </section>
          )}
        </div>
      </div>
    </>
  )
}
