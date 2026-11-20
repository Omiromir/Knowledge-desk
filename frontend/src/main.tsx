import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { AuthGate } from './features/auth/AuthGate'
import { readRequestPreference } from './features/requests/history'

const [section, query] = window.location.hash.split('?', 2)
if (section === '#requests') {
  const parameters = new URLSearchParams(query)
  if (!parameters.has('status') && !parameters.has('sort')) {
    const saved = new URLSearchParams(readRequestPreference('view'))
    for (const key of ['status', 'sort']) {
      const value = saved.get(key)
      if (value) parameters.set(key, value)
    }
    if (parameters.has('status') || parameters.has('sort')) {
      window.history.replaceState(window.history.state, '', `#requests?${parameters}`)
    }
  }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthGate />
  </StrictMode>,
)
