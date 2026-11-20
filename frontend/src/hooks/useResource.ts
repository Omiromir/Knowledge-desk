import { useEffect, useState } from 'react'
import { errorMessage } from '../api/client'

interface Resource<T> {
  data: T | null
  loading: boolean
  error: string
}

export function useResource<T>(load: (signal: AbortSignal) => Promise<T>) {
  const [state, setState] = useState<Resource<T>>({
    data: null,
    loading: true,
    error: '',
  })
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    load(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted)
          setState({ data, loading: false, error: '' })
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted)
          setState({ data: null, loading: false, error: errorMessage(failure) })
      })
    return () => controller.abort()
  }, [load, revision])

  function reload() {
    setState({ data: null, loading: true, error: '' })
    setRevision((value) => value + 1)
  }

  return { ...state, reload }
}
