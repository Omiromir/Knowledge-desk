import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { AuthGate } from './features/auth/AuthGate'
import { readRequestPreference } from './features/requests/history'
import { requestHashWithSavedView } from './navigation/routes'

const hash = requestHashWithSavedView(window.location.hash, readRequestPreference('view'))
if (hash !== window.location.hash) window.history.replaceState(window.history.state, '', hash)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthGate />
  </StrictMode>,
)
