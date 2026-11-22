import { useEffect, useState } from 'react'
import { readRequestPreference, saveRequestPreference } from '../features/requests/history'
import { readHash, requestViewQuery } from './routes'

function readDeskRoute(hash: string, requestView: string, graphView = false) {
  const { section, parameters } = readHash(hash)
  return {
    section,
    parameters,
    requestView: section === '#requests' ? requestViewQuery(parameters) : requestView,
    graphView: section === '#documents' ? parameters.get('view') === 'graph' : graphView,
  }
}

export function useDeskRoute() {
  const [route, setRoute] = useState(() => readDeskRoute(window.location.hash, readRequestPreference('view')))

  useEffect(() => {
    function update(event: HashChangeEvent) {
      const hash = new URL(event.newURL).hash
      setRoute((previous) => readDeskRoute(hash, previous.requestView, previous.graphView))
    }
    window.addEventListener('hashchange', update)
    return () => window.removeEventListener('hashchange', update)
  }, [])

  useEffect(() => {
    saveRequestPreference('view', route.requestView)
  }, [route.requestView])

  return route
}
