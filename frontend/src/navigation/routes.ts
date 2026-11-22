export const profileHref = '#profile'

const requestViewKeys = ['status', 'sort'] as const

export function readHash(hash: string) {
  const [section, query] = hash.split('?', 2)
  return { section, parameters: new URLSearchParams(query) }
}

type RequestTarget = 'compose' | { requestId: string } | { documentId: string }

export function requestsHref(view: string | URLSearchParams = '', target?: RequestTarget) {
  const prefix = target === 'compose' ? 'compose'
    : target && 'requestId' in target ? `id=${encodeURIComponent(target.requestId)}`
    : target ? `document=${encodeURIComponent(target.documentId)}` : ''
  const query = [prefix, String(view)].filter(Boolean).join('&')
  return query ? `#requests?${query}` : '#requests'
}

export function requestViewQuery(parameters: URLSearchParams) {
  const view = new URLSearchParams()
  for (const key of requestViewKeys) {
    const value = parameters.get(key)
    if (value) view.set(key, value)
  }
  return view.toString()
}

export function requestHashWithSavedView(hash: string, savedView: string) {
  const { section, parameters } = readHash(hash)
  if (section !== '#requests' || requestViewKeys.some((key) => parameters.has(key))) return hash
  const saved = new URLSearchParams(savedView)
  for (const key of requestViewKeys) {
    const value = saved.get(key)
    if (value) parameters.set(key, value)
  }
  return requestViewKeys.some((key) => parameters.has(key)) ? requestsHref(parameters) : hash
}

export function consultationHref(id?: string) {
  return id ? `#ask?consultation=${encodeURIComponent(id)}` : '#ask?consultation'
}
