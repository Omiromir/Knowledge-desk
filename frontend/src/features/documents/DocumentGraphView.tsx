import { Component, lazy, Suspense, type ComponentProps, type ReactNode } from 'react'
import { AsyncState } from '../../components/AsyncState'
import './documentGraph.css'

const Graph = lazy(() => import('./DocumentGraph.tsx'))

class GraphBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    if (this.state.failed) {
      return <AsyncState title="Не удалось открыть граф" error
        description="Повторное открытие обновит страницу."
        onRetry={() => window.location.reload()} />
    }
    return this.props.children
  }
}

export function DocumentGraphView(props: ComponentProps<typeof Graph>) {
  return (
    <GraphBoundary>
      <Suspense fallback={<AsyncState title="Открываем граф…" loading />}>
        <Graph {...props} />
      </Suspense>
    </GraphBoundary>
  )
}
