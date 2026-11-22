import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type SimulationNodeDatum } from 'd3-force'
import { select } from 'd3-selection'
import 'd3-transition'
import { zoom, zoomIdentity, zoomTransform, type ZoomTransform } from 'd3-zoom'
import { useId, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import type { DocumentRecord } from '../../api/types'
import { AsyncState } from '../../components/AsyncState'
import { Icon } from '../../components/Icons'
import { documentEdges, type DocumentEdge, type GraphDepth } from './documentEdges'

type Node = SimulationNodeDatum & { x: number; y: number; index: number }
type Camera = {
  fit: () => void
  fitDepth: () => void
  scaleBy: (amount: number) => void
  pan: (dx: number, dy: number) => void
  reveal: (index: number) => void
}

const minZoom = .025
const maxZoom = 4
const markerHalfHeight = 8
const ringSpacing = 96
const ringLabelAngle = -Math.PI / 9
const russianNumber = new Intl.NumberFormat('ru')
const documentPlural = new Intl.PluralRules('ru')
const documentForms = { one: 'документ', few: 'документа', many: 'документов', other: 'документов', zero: 'документов', two: 'документа' }

function shortTitle(title: string) {
  return title.length > 35 ? `${title.slice(0, 34).trimEnd()}…` : title
}

type GraphProps = {
  documents: DocumentRecord[]
  centerId?: string
  depth?: GraphDepth
  matching?: Set<string> | null
  hrefFor: (id: string) => string
  onOpen?: (link: HTMLAnchorElement | SVGAElement) => void
}

const edgesByCatalog = new WeakMap<DocumentRecord[], DocumentEdge[]>()

type GraphEdge = DocumentEdge & { step?: number }

function graphFor(documents: DocumentRecord[], centerId: string | undefined): {
  documents: DocumentRecord[]
  edges: GraphEdge[]
  steps: number[] | null
  parents: number[] | null
} {
  let edges = edgesByCatalog.get(documents)
  if (!edges) {
    edges = documentEdges(documents)
    edgesByCatalog.set(documents, edges)
  }
  if (centerId === undefined) return { documents, edges, steps: null, parents: null }
  const center = documents.findIndex(document => document.id === centerId)
  if (center < 0) return { documents: [], edges: [], steps: [], parents: [] }
  const adjacent = documents.map(() => [] as number[])
  for (const { source, target } of edges) {
    adjacent[source].push(target)
    adjacent[target].push(source)
  }
  const indices = [center]
  const parents = [-1]
  const reached = new Map([[center, { index: 0, distance: 0 }]])
  for (let cursor = 0; cursor < indices.length; cursor++) {
    const node = indices[cursor]
    const distance = reached.get(node)!.distance
    if (distance === 3) break
    for (const target of adjacent[node]) {
      if (reached.has(target)) continue
      reached.set(target, { index: indices.length, distance: distance + 1 })
      indices.push(target)
      parents.push(cursor)
    }
  }
  return {
    documents: indices.map(index => documents[index]),
    steps: indices.map(index => reached.get(index)!.distance),
    parents,
    edges: edges.flatMap(({ source, target, kind }) => {
      const from = reached.get(source)
      const to = reached.get(target)
      if (!from || !to) return []
      const step = 1 + Math.min(from.distance, to.distance)
      return step <= 3 ? [{ source: from.index, target: to.index, kind, step }] : []
    }),
  }
}

export default function DocumentGraph({ documents, centerId, depth = 1, ...props }: GraphProps) {
  const graph = useMemo(() => graphFor(documents, centerId), [documents, centerId])
  if (centerId !== undefined && !graph.edges.length) {
    return <AsyncState title="Связанных документов пока нет" />
  }
  return <GraphCanvas {...props} {...graph} depth={depth} />
}

function ringNodes(steps: number[]): Node[] {
  const counts = [0, 0, 0, 0]
  const offsets = [0, 0, 0, 0]
  for (const step of steps) counts[step]++
  return steps.map((step, index) => {
    if (step === 0) return { index, x: 0, y: 0 }
    const count = counts[step]
    const spacing = 2 * Math.PI / count
    // With two neighbors, keep the lower one clear of the zoom buttons.
    const phase = count === 2 ? Math.PI / 7 : ringLabelAngle
    const angle = count === 1 ? -2 * Math.PI / 3 : phase + (offsets[step]++ + .5) * spacing
    return { index, x: Math.cos(angle) * step * ringSpacing, y: Math.sin(angle) * step * ringSpacing }
  })
}

function GraphCanvas({ documents, edges, steps, parents, depth, matching, hrefFor, onOpen }: Omit<GraphProps, 'centerId' | 'depth'> & {
  edges: GraphEdge[]
  steps: number[] | null
  parents: number[] | null
  depth: GraphDepth
}) {
  const centered = steps !== null
  const furthestStep = steps?.at(-1) ?? 0
  const neighbors = useMemo(() => {
    const nodes = documents.map(() => new Set<number>())
    for (const { source, target } of edges) {
      nodes[source].add(target)
      nodes[target].add(source)
    }
    return nodes
  }, [documents, edges])
  const radii = useMemo(() => neighbors.map((node, index) => centered && index === 0
    ? 13 : 4.6 + Math.min(7, Math.sqrt(node.size) * 2.2)), [centered, neighbors])
  const viewportRef = useRef<SVGSVGElement>(null)
  const worldRef = useRef<SVGGElement>(null)
  const camera = useRef<Camera>(null)
  const visibleDepth = useRef(depth)
  const [hovered, setHovered] = useState<string | null>(null)
  const [focused, setFocused] = useState<string | null>(null)
  const instructionsId = useId()
  const inDepth = (index: number) => (steps?.[index] ?? 0) <= depth
  const matches = (index: number) => inDepth(index) && (!matching || matching.has(documents[index].id))
  const activeId = centered ? hovered ?? focused : focused ?? hovered
  const activeNode = documents.findIndex(document => document.id === activeId)
  const active = activeNode >= 0 && (!centered || activeNode !== 0) && matches(activeNode) ? activeNode : null
  const path = new Set<number>()
  if (parents && active !== null) {
    for (let index = active; index >= 0; index = parents[index]) path.add(index)
  }
  const highlightedNode = (index: number) => parents ? path.has(index) :
    active === index || (active !== null && neighbors[active].has(index))
  const highlightedEdge = (source: number, target: number) => parents
    ? path.has(source) && path.has(target) && (parents[source] === target || parents[target] === source)
    : active === source || active === target
  const count = (steps?.filter(step => step <= depth).length ?? matching?.size ?? documents.length) - Number(centered)
  const plural = documentPlural.select(count)

  useLayoutEffect(() => {
    const viewport = viewportRef.current
    const world = worldRef.current
    if (!viewport || !world) return
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)')
    const surface = select(viewport)
    const anchors = world.querySelectorAll<SVGAElement>('.document-graph-node')
    const markers = world.querySelectorAll<SVGGElement>('.document-graph-mark')
    const hits = world.querySelectorAll<SVGCircleElement>('.document-graph-hit')
    const lines = world.querySelectorAll<SVGLineElement>('.document-graph-edge')
    const titles = world.querySelectorAll<SVGTextElement>('.document-graph-title')
    const nodes: Node[] = steps ? ringNodes(steps) : documents.map((_, index) => ({ index, x: NaN, y: NaN }))
    const labelPriority = [...nodes].sort((a, b) => radii[b.index] - radii[a.index])
    const textMeasure = document.createElement('canvas').getContext('2d')
    if (textMeasure) textMeasure.font = '11px "Segoe UI", system-ui, sans-serif'
    const titleWidths = documents.map(({ title }) => textMeasure?.measureText(shortTitle(title)).width ?? shortTitle(title).length * 6)
    const shown = new Set<number>()
    const links = edges.map(edge => ({ ...edge, source: nodes[edge.source], target: nodes[edge.target] }))
    const simulation = steps ? null : forceSimulation(nodes).stop()
      .alphaDecay(.07)
      .force('links', forceLink(links).distance(edge => edge.kind === 'link' ? 96 : 121)
        .strength(edge => edge.kind === 'link' ? .35 : .20))
      .force('repulsion', forceManyBody().strength(-645).distanceMax(390))
      .force('spacing', forceCollide(36).iterations(2))
      .force('horizontal', forceX().strength(.040))
      .force('vertical', forceY().strength(.064))
    let width = 0
    let height = 0
    let labelScale = 0
    let markerRadii = radii
    let manualCamera = false
    const behavior = zoom<SVGSVGElement, unknown>()
      .extent(() => [[0, 0], [width, height]])
      .scaleExtent([minZoom, maxZoom])
      .clickDistance(5)
      .duration(motion.matches ? 0 : 220)
      .on('zoom', (event) => {
        if (event.sourceEvent) manualCamera = true
        world.setAttribute('transform', event.transform.toString())
        placeLabels(event.transform)
      })
    surface.call(behavior)

    function placeLabels(transform: ZoomTransform) {
      if (!width || !height) return
      const scale = transform.k
      if (labelScale !== scale) {
        labelScale = scale
        world!.style.setProperty('--graph-label-size', `${11 / scale}px`)
        world!.style.setProperty('--graph-label-stroke', `${3 / scale}px`)
        markerRadii = radii.map((radius, index) => {
          const isCenter = centered && index === 0
          return Math.min(isCenter ? 14 : 11, Math.max(isCenter ? 10 : 6, radius * scale + 1))
        })
        markerRadii.forEach((radius, index) => {
          markers[index].setAttribute('transform', `scale(${radius / (markerHalfHeight * scale)})`)
          hits[index].setAttribute('r', String(radius / scale + 2))
          titles[index].setAttribute('y', String((radius + 11) / scale))
        })
      }
      const boxes = Array.from({ length: Math.min(furthestStep, visibleDepth.current) }, (_, index) => {
        const radius = (index + 1) * ringSpacing
        const [x, y] = transform.apply([Math.cos(ringLabelAngle) * radius, Math.sin(ringLabelAngle) * radius])
        return { left: x - 7, right: x + 7, top: y - 8, bottom: y + 8 }
      })
      const next = new Set<number>()
      const limit = width < 520 ? 7 : 40
      const projected = labelPriority.map(({ index, x, y }) => ({
        index, x: transform.applyX(x), y: transform.applyY(y), radius: markerRadii[index],
      }))
      const visibleDots = projected.filter(({ index, x, y }) => (steps?.[index] ?? 0) <= visibleDepth.current
        && x > 0 && x < width && y > 0 && y < height)
      for (const { index, x, y, radius } of visibleDots) {
        if (next.size >= limit) break
        const bottom = y + radius + 16
        const half = titleWidths[index] / 2 + 4
        const box = { left: x - half, right: x + half, top: bottom - 16, bottom }
        const controlsWidth = width <= 720 ? 152 : 120
        const footerOverlap = centered && (box.bottom > height - 64 && box.right > width - controlsWidth
          || width > 720 && box.bottom > height - 44 && box.left < 330)
        if (box.left < 6 || box.right > width - 6 || box.top < 26
          || box.bottom > height - (centered ? 12 : 62) || footerOverlap) continue
        if (boxes.some(other => box.left < other.right && box.right > other.left
          && box.top < other.bottom && box.bottom > other.top)) continue
        if (visibleDots.some(dot => dot.index !== index && dot.x + dot.radius > box.left && dot.x - dot.radius < box.right
          && dot.y + dot.radius > box.top && dot.y - dot.radius < box.bottom)) continue
        next.add(index)
        boxes.push(box)
      }
      for (const index of shown) if (!next.has(index)) titles[index].removeAttribute('data-visible')
      for (const index of next) if (!shown.has(index)) titles[index].setAttribute('data-visible', '')
      shown.clear()
      for (const index of next) shown.add(index)
    }

    function fit(duration = 0) {
      if (!width || !height) return
      const outerRing = Math.min(furthestStep, visibleDepth.current) * ringSpacing
      let left = centered ? -outerRing : Infinity
      let right = centered ? outerRing : -Infinity
      let top = left
      let bottom = right
      if (!centered) {
        for (const { x, y } of nodes) {
          left = Math.min(left, x)
          right = Math.max(right, x)
          top = Math.min(top, y)
          bottom = Math.max(bottom, y)
        }
      }
      const innerWidth = width - Math.min(190, width * .27)
      const innerHeight = height - Math.min(154, height * .29)
      const centerX = centered ? 0 : (left + right) / 2
      const centerY = centered ? 0 : (top + bottom) / 2
      const spanX = 2 * Math.max(right - centerX, centerX - left) + 24
      const spanY = 2 * Math.max(bottom - centerY, centerY - top) + 24
      const k = Math.max(minZoom, Math.min(1.36, innerWidth / spanX, innerHeight / spanY))
      const transform = zoomIdentity
        .translate(width / 2 - centerX * k, height / 2 - centerY * k)
        .scale(k)
      if (duration && !motion.matches) surface.transition().duration(duration).call(behavior.transform, transform)
      else surface.call(behavior.transform, transform)
    }

    function draw() {
      for (const { index, x, y } of nodes) anchors[index]?.setAttribute('transform', `translate(${x},${y})`)
      links.forEach(({ source, target }, index) => {
        const line = lines[index]
        if (!line) return
        line.setAttribute('x1', String(source.x))
        line.setAttribute('y1', String(source.y))
        line.setAttribute('x2', String(target.x))
        line.setAttribute('y2', String(target.y))
      })
      if (manualCamera) placeLabels(zoomTransform(viewport!))
      else fit()
    }

    camera.current = {
      fit: () => { manualCamera = true; fit(200) },
      fitDepth: () => { manualCamera = false; fit(280) },
      scaleBy: (amount) => {
        manualCamera = true
        if (motion.matches) surface.call(behavior.scaleBy, amount)
        else surface.transition().duration(180).call(behavior.scaleBy, amount)
      },
      pan: (dx, dy) => {
        manualCamera = true
        const { k } = zoomTransform(viewport)
        surface.call(behavior.translateBy, dx / k, dy / k)
      },
      reveal: (index) => {
        const { x, y } = nodes[index]
        const [screenX, screenY] = zoomTransform(viewport).apply([x, y])
        if (centered) {
          const canvas = viewport.getBoundingClientRect()
          const overlays = viewport.parentElement?.querySelectorAll('.document-graph-legend, .document-graph-controls')
          const clientX = canvas.left + screenX
          const clientY = canvas.top + screenY
          const covered = Array.from(overlays ?? []).some(overlay => {
            const bounds = overlay.getBoundingClientRect()
            return clientX > bounds.left - 6 && clientX < bounds.right + 6
              && clientY > bounds.top - 6 && clientY < bounds.bottom + 6
          })
          if (screenX > 12 && screenX < width - 12 && screenY > 12 && screenY < height - 12 && !covered) return
          manualCamera = false
          fit()
          return
        }
        if (screenX > 26 && screenX < width - 26 && screenY > 26 && screenY < height - 58) return
        manualCamera = true
        surface.call(behavior.translateTo, x, y)
      },
    }

    function settle() {
      if (!simulation) return
      if (!viewport?.clientWidth || document.hidden) {
        simulation.stop()
      } else if (motion.matches) {
        simulation.stop()
        if (simulation.alpha() > simulation.alphaMin()) simulation.tick(98)
        draw()
      } else if (simulation.alpha() > simulation.alphaMin()) {
        simulation.restart()
      }
    }

    function resize() {
      const nextWidth = viewport!.clientWidth
      const nextHeight = viewport!.clientHeight
      if (nextWidth && nextHeight) {
        const previous = zoomTransform(viewport!)
        if (width && height && manualCamera && (nextWidth !== width || nextHeight !== height)) {
          surface.call(behavior.transform, zoomIdentity.translate(
            previous.x + (nextWidth - width) / 2, previous.y + (nextHeight - height) / 2,
          ).scale(previous.k))
        }
        width = nextWidth
        height = nextHeight
        if (manualCamera) placeLabels(zoomTransform(viewport!))
        else fit()
      }
      settle()
    }

    function motionChanged() {
      behavior.duration(motion.matches ? 0 : 220)
      if (motion.matches) {
        surface.interrupt()
        if (centered && !manualCamera) fit()
      }
      settle()
    }

    simulation?.tick(20).on('tick', draw)
    resize()
    draw()
    const observer = new ResizeObserver(resize)
    observer.observe(viewport)
    motion.addEventListener('change', motionChanged)
    document.addEventListener('visibilitychange', settle)
    return () => {
      camera.current = null
      simulation?.stop()
      observer.disconnect()
      motion.removeEventListener('change', motionChanged)
      document.removeEventListener('visibilitychange', settle)
      surface.interrupt().on('.zoom', null)
    }
  }, [centered, documents, edges, furthestStep, radii, steps])

  useLayoutEffect(() => {
    if (visibleDepth.current === depth) return
    visibleDepth.current = depth
    camera.current?.fitDepth()
  }, [depth])

  function keyboardNavigation(event: KeyboardEvent<SVGSVGElement>) {
    if (event.ctrlKey || event.altKey || event.metaKey) return
    const step = event.shiftKey ? 100 : 40
    const controls = camera.current
    if (!controls) return
    switch (event.key) {
      case '+': case '=': controls.scaleBy(1.25); break
      case '-': controls.scaleBy(.8); break
      case 'Home': controls.fit(); break
      case 'ArrowUp': controls.pan(0, step); break
      case 'ArrowDown': controls.pan(0, -step); break
      case 'ArrowLeft': controls.pan(step, 0); break
      case 'ArrowRight': controls.pan(-step, 0); break
      default: return
    }
    event.preventDefault()
  }

  return (
    <div className="document-graph load-result">
      <svg className="document-graph-viewport" ref={viewportRef} tabIndex={0} role="group"
        data-single-match={matching?.size === 1}
        aria-label={centered ? 'Локальный граф документа' : 'Граф документов'}
        aria-describedby={instructionsId} onKeyDown={keyboardNavigation}>
        <g ref={worldRef}>
          <g aria-hidden="true">
            {Array.from({ length: furthestStep }, (_, index) => index + 1).map(step => {
              const radius = step * ringSpacing
              return (
                <g key={step} className="document-graph-ring" data-step={step} data-depth-hidden={step > depth}>
                  <circle r={radius} vectorEffect="non-scaling-stroke" />
                  <text x={Math.cos(ringLabelAngle) * radius} y={Math.sin(ringLabelAngle) * radius}
                    textAnchor="middle" dominantBaseline="central">{step}</text>
                </g>
              )
            })}
            {edges.map(({ source, target, kind, step = 0 }) => {
              const highlighted = highlightedEdge(source, target)
              return (
                <line key={`${source}:${target}`} className="document-graph-edge" data-kind={kind}
                  data-depth-hidden={step > depth}
                  data-dimmed={(!matches(source) && !matches(target)) || (active !== null && !highlighted)}
                  data-highlighted={highlighted} vectorEffect="non-scaling-stroke" />
              )
            })}
          </g>
          {documents.map((document, index) => {
            const matched = matches(index)
            const center = centered && index === 0
            const navigable = matched && !center
            const radius = radii[index]
            const step = steps?.[index]
            const highlighted = highlightedNode(index)
            return (
              <a key={document.id} className="document-graph-node"
                aria-label={document.title} aria-hidden={!matched || undefined}
                aria-description={step ? `Шаг ${step} от открытого документа` : undefined}
                role={center ? 'img' : undefined} aria-current={center ? 'page' : undefined}
                href={navigable ? hrefFor(document.id) : undefined}
                tabIndex={navigable ? 0 : -1}
                data-center={center}
                data-step={step} data-depth-hidden={!inDepth(index)}
                data-matched={matched} data-connected={neighbors[index].size > 0}
                data-dimmed={!matched || (active !== null && !highlighted)}
                data-highlighted={highlighted} data-active={active === index}
                onMouseEnter={center ? undefined : () => setHovered(document.id)} onMouseLeave={() => setHovered(null)}
                onFocus={(event) => {
                  setFocused(document.id)
                  if (event.currentTarget.matches(':focus-visible')) {
                    if (centered) setHovered(null)
                    camera.current?.reveal(index)
                  }
                }}
                onBlur={() => setFocused(null)}
                onClick={onOpen && navigable ? (event) => onOpen(event.currentTarget) : undefined}>
                <title>{document.title}</title>
                <circle className="document-graph-hit" r={radius + 2} vectorEffect="non-scaling-stroke" aria-hidden="true" />
                <g className="document-graph-mark" aria-hidden="true">
                  <path className="document-graph-mark-sheet" d="M-4.6-7.4H1.4L5.8-3v9a1.4 1.4 0 0 1-1.4 1.4h-9A1.4 1.4 0 0 1-6 6v-12a1.4 1.4 0 0 1 1.4-1.4Z" />
                  <path className="document-graph-mark-fold" d="M1.4-7.4V-4a1 1 0 0 0 1 1h3.4Z" />
                  <path className="document-graph-mark-lines" d="M-3.3.35h6.6m-6.6 3.35h4.15" />
                </g>
                <text className="document-graph-title" y={radius + 15} textAnchor="middle" aria-hidden="true">
                  {shortTitle(document.title)}
                </text>
              </a>
            )
          })}
        </g>
      </svg>
      <span className="document-graph-instructions" id={instructionsId}>
        {centered && 'Номер кольца показывает число шагов от открытого документа. Наведение или фокус на узле выделяет путь к нему. '}
        Перемещение стрелками или перетаскиванием. Масштаб колёсиком, жестом или клавишами плюс и минус.
        Клавиша Home показывает весь граф. По Tab можно перейти к документам.
      </span>
      {centered && <span className="document-graph-instructions" role="status">
        {focused && focused === activeId && [...path].reverse().map(index => documents[index].title).join(' → ')}
      </span>}
      <div className="document-graph-legend">
        <span role="status">{russianNumber.format(count)}{centered && (plural === 'one' ? ' связанный' : ' связанных')} {documentForms[plural]}</span>
        {edges.some(edge => edge.kind === 'link') && <span className="graph-legend-link">Ссылки</span>}
        {edges.some(edge => edge.kind === 'text') && <span className="graph-legend-text">По тексту</span>}
      </div>
      <div className="document-graph-controls" role="group" aria-label="Управление графом">
        <button className="icon-button" type="button" aria-label="Уменьшить масштаб" title="Уменьшить масштаб"
          onClick={() => camera.current?.scaleBy(.8)}><Icon name="minus" /></button>
        <button className="icon-button" type="button" aria-label="Увеличить масштаб" title="Увеличить масштаб"
          onClick={() => camera.current?.scaleBy(1.25)}><Icon name="plus" /></button>
        <button className="icon-button" type="button" aria-label="Показать весь граф" title="Показать весь граф"
          onClick={() => camera.current?.fit()}><Icon name="fit" /></button>
      </div>
    </div>
  )
}
