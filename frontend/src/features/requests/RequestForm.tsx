import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ApiError, errorMessage } from '../../api/client'
import { createRequest } from '../../api/requests'
import type { RequestRecord } from '../../api/types'
import { SummaryPreview } from './SummaryPreview'

export function RequestForm({
  onCreated,
  requestListHref,
  documentId,
  documentTitle,
  documentsLoading,
  suggestedQuestion,
  onUseSuggestedQuestion,
}: {
  onCreated: (record: RequestRecord) => void
  requestListHref: string
  documentId: string | null
  documentTitle: string | undefined
  documentsLoading: boolean
  suggestedQuestion: string
  onUseSuggestedQuestion: () => void
}) {
  const [name, setName] = useState('')
  const [question, setQuestion] = useState('')
  const [fields, setFields] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [result, setResult] = useState<RequestRecord | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const submittingRef = useRef(false)
  const focusError = useRef(false)
  const nameRef = useRef<HTMLInputElement>(null)
  const questionRef = useRef<HTMLTextAreaElement>(null)
  const dirty = Boolean((name || question) && !result)
  const waitingForDocument = Boolean(documentId && !documentTitle)

  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  useEffect(() => {
    if (submitting || !focusError.current) return
    focusError.current = false
    if (fields.name) nameRef.current?.focus()
    else if (fields.question) questionRef.current?.focus()
  }, [fields, submitting])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submittingRef.current || waitingForDocument) return
    setResult(null)
    setError('')
    const errors: Record<string, string> = {}
    if (!name.trim()) errors.name = 'Укажите ваше имя.'
    if (!question.trim()) errors.question = 'Напишите ваш вопрос.'
    setFields(errors)
    if (errors.name || errors.question) {
      focusError.current = true
      return
    }
    submittingRef.current = true
    setSubmitting(true)
    try {
      const record = await createRequest({
        name,
        question,
        document_id: documentId ?? undefined,
      })
      setResult(record)
      onCreated(record)
    } catch (failure) {
      setError(errorMessage(failure))
      if (failure instanceof ApiError) {
        focusError.current = true
        setFields(failure.fields)
      }
    } finally {
      submittingRef.current = false
      setSubmitting(false)
    }
  }

  function changeQuestion(text: string) {
    setQuestion(text)
    setResult(null)
    setFields((previous) => ({ ...previous, question: '' }))
  }

  return (
    <form
      className="request-form"
      onSubmit={submit}
      noValidate
      aria-busy={submitting}
    >
      <h2>Новая заявка</h2>
      {documentId ? (
        <p className="request-context" role="status">
          {documentTitle ? (
            <>
              Документ:{' '}
              <a href={`#documents?id=${encodeURIComponent(documentId)}`}>
                {documentTitle}
              </a>
            </>
          ) : documentsLoading ? (
            'Загружаем документ…'
          ) : (
            <>
              Документ недоступен. <a href={requestListHref}>Создать общую заявку</a>
            </>
          )}
        </p>
      ) : null}
      <div className="field">
        <label htmlFor="request-name">Ваше имя</label>
        <input
          ref={nameRef}
          id="request-name"
          name="name"
          autoComplete="name"
          value={name}
          disabled={submitting}
          aria-required="true"
          aria-invalid={Boolean(fields.name)}
          aria-describedby={fields.name ? 'name-error' : undefined}
          onChange={(event) => {
            setName(event.target.value)
            setResult(null)
            setFields((previous) => ({ ...previous, name: '' }))
          }}
          placeholder="Как к вам обращаться…"
        />
        {fields.name ? (
          <p id="name-error" className="field-error" role="alert">
            {fields.name}
          </p>
        ) : null}
      </div>
      <div className="field">
        <label htmlFor="request-question">Ваш вопрос</label>
        {suggestedQuestion && suggestedQuestion !== question.trim() && (
          <div className="request-context">
            <p>{suggestedQuestion}</p>
            <button
              className="button secondary"
              type="button"
              disabled={submitting}
              onClick={() => {
                changeQuestion(suggestedQuestion)
                onUseSuggestedQuestion()
                questionRef.current?.focus()
              }}
            >
              {question.trim() ? 'Заменить вопрос' : 'Подставить вопрос'}
            </button>
          </div>
        )}
        <textarea
          ref={questionRef}
          id="request-question"
          name="question"
          autoComplete="off"
          rows={6}
          value={question}
          disabled={submitting}
          aria-required="true"
          aria-invalid={Boolean(fields.question)}
          aria-describedby={fields.question ? 'question-error' : undefined}
          onChange={(event) => changeQuestion(event.target.value)}
          placeholder="Что вы хотите узнать…"
        />
        {fields.question ? (
          <p id="question-error" className="field-error" role="alert">
            {fields.question}
          </p>
        ) : null}
      </div>
      {error ? (
        <div className="form-error" role="alert">
          <strong>Отправка не подтверждена</strong>
          <p>{error}</p>
        </div>
      ) : null}
      <button
        className="button primary submit-button"
        type="submit"
        disabled={submitting || waitingForDocument}
      >
        {submitting ? (
          <>
            <span className="loading-mark" aria-hidden="true" />
            Отправляем…
          </>
        ) : (
          'Отправить заявку'
        )}
      </button>
      <div
        className="submission-result"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        {result && (result.document_id ?? null) === documentId
          ? 'Заявка сохранена'
          : null}
      </div>
      <SummaryPreview question={question} submitting={submitting} />
    </form>
  )
}
