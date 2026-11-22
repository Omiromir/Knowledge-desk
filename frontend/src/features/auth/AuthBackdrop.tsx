import { useEffect, useRef } from 'react'

const tile = 24
const artWidth = 1728
const artHeight = 1152
const lightRadius = 3.2
const turnDuration = 180
const lifetime = 1000
const pixelBudget = 2_000_000

type Point = { x: number; y: number }
type LitPage = { col: number; row: number; turnedAt: number; litAt: number; intensity: number }
type Illuminate = (col: number, row: number, intensity: number) => void

function documentTile() {
  const stamp = document.createElement('canvas')
  stamp.width = stamp.height = tile * 2
  const paint = stamp.getContext('2d')
  if (!paint) return stamp
  paint.scale(2, 2)
  paint.fillStyle = '#d2bffa'
  paint.fill(new Path2D('M4 2h10l6 6v14H4z'))
  paint.fillStyle = '#f0e2ff'
  paint.fill(new Path2D('M14 2l6 6h-6z'))
  paint.fillStyle = '#584474'
  paint.fillRect(7, 12, 10, 1.3)
  paint.fillRect(7, 16, 7, 1.3)
  return stamp
}

function lightPages(from: Point, to: Point, illuminate: Illuminate) {
  const dx = to.x - from.x
  const dy = to.y - from.y
  const lengthSquared = dx * dx + dy * dy
  const radiusSquared = lightRadius * lightRadius
  const firstCol = Math.max(0, Math.floor(Math.min(from.x, to.x) - lightRadius))
  const lastCol = Math.min(artWidth / tile - 1, Math.floor(Math.max(from.x, to.x) + lightRadius))
  const firstRow = Math.max(0, Math.floor(Math.min(from.y, to.y) - lightRadius))
  const lastRow = Math.min(artHeight / tile - 1, Math.floor(Math.max(from.y, to.y) + lightRadius))

  for (let row = firstRow; row <= lastRow; row++) {
    for (let col = firstCol; col <= lastCol; col++) {
      const x = col + .5 - from.x
      const y = row + .5 - from.y
      const along = lengthSquared ? Math.max(0, Math.min(1, (x * dx + y * dy) / lengthSquared)) : 0
      const distanceSquared = (x - along * dx) ** 2 + (y - along * dy) ** 2
      if (distanceSquared < radiusSquared) illuminate(col, row, 1 - distanceSquared / radiusSquared)
    }
  }
}

function brightness(page: LitPage, now: number) {
  const remaining = Math.max(0, 1 - (now - page.litAt) / lifetime)
  return page.intensity * remaining * remaining * (3 - 2 * remaining)
}

export function AuthBackdrop() {
  const stageRef = useRef<HTMLDivElement>(null)
  const lightRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    if (!stageRef.current || !lightRef.current) return
    const stage = stageRef.current
    const light = lightRef.current
    const motion = matchMedia('(any-hover: hover) and (any-pointer: fine) and (prefers-reduced-motion: no-preference)')
    let context: CanvasRenderingContext2D | null = null
    let stamp: HTMLCanvasElement | null = null
    let bounds = stage.getBoundingClientRect()
    let width = 0
    let height = 0
    let cell = 0
    let originX = 0
    let originY = 0
    let frame = 0
    let previous: Point | null = null
    let pending: Point | null = null
    const lit = new Map<number, LitPage>()

    function draw(now: number) {
      frame = 0
      if (!context || !stamp) return
      const point = pending
      if (point) {
        pending = null
        lightPages(previous ?? point, point, (col, row, intensity) => {
          const key = row * (artWidth / tile) + col
          const page = lit.get(key)
          const current = page ? brightness(page, now) : 0
          if (intensity <= current) return
          lit.set(key, { col, row, turnedAt: current && page ? page.turnedAt : now, litAt: now, intensity })
        })
        previous = point
      }
      for (const [key, page] of lit) {
        const x = originX + page.col * cell
        const y = originY + page.row * cell
        const opacity = brightness(page, now)
        context.clearRect(x, y, cell, cell)
        if (opacity === 0) {
          lit.delete(key)
          continue
        }
        const opened = 1 - (1 - Math.min(1, (now - page.turnedAt) / turnDuration)) ** 3
        const pageWidth = cell * (.36 + .64 * opened)
        context.globalAlpha = .8 * opacity * (.65 + .35 * opened)
        // Keep the left edge in place while the page swings open.
        context.drawImage(stamp, x + (cell - pageWidth) * 4 / tile, y, pageWidth, cell)
      }
      if (lit.size) frame = requestAnimationFrame(draw)
    }

    function forgetPointer() {
      previous = null
      pending = null
    }

    function stop() {
      cancelAnimationFrame(frame)
      frame = 0
      if (lit.size) context?.clearRect(0, 0, width, height)
      lit.clear()
      forgetPointer()
    }

    function resizeCanvas() {
      if (!context || !width || !height) return
      const scale = Math.min(window.devicePixelRatio || 1, 1.5, Math.sqrt(pixelBudget / (width * height)))
      light.width = Math.max(1, Math.floor(width * scale))
      light.height = Math.max(1, Math.floor(height * scale))
      context.setTransform(light.width / width, 0, 0, light.height / height, 0, 0)
    }

    function measure() {
      bounds = stage.getBoundingClientRect()
      if (stage.clientWidth === width && stage.clientHeight === height) return
      stop()
      width = stage.clientWidth
      height = stage.clientHeight
      const scale = Math.max(width / artWidth, height / artHeight)
      cell = tile * scale
      originX = (width - artWidth * scale) / 2
      originY = (height - artHeight * scale) / 2
      resizeCanvas()
    }

    function move(event: PointerEvent) {
      if (!motion.matches || event.pointerType === 'touch' || document.hidden || !cell) return
      if (!context) {
        context = light.getContext('2d')
        if (!context) return
        stamp = documentTile()
        resizeCanvas()
      }

      pending = {
        x: (event.clientX - bounds.left - originX) / cell,
        y: (event.clientY - bounds.top - originY) / cell,
      }
      if (!frame) frame = requestAnimationFrame(draw)
    }

    measure()
    const resize = new ResizeObserver(measure)
    resize.observe(stage)
    stage.addEventListener('pointerenter', measure)
    stage.addEventListener('pointermove', move, { passive: true })
    stage.addEventListener('pointerleave', forgetPointer)
    window.addEventListener('blur', stop)
    document.addEventListener('visibilitychange', stop)
    motion.addEventListener('change', stop)
    return () => {
      stop()
      resize.disconnect()
      stage.removeEventListener('pointerenter', measure)
      stage.removeEventListener('pointermove', move)
      stage.removeEventListener('pointerleave', forgetPointer)
      window.removeEventListener('blur', stop)
      document.removeEventListener('visibilitychange', stop)
      motion.removeEventListener('change', stop)
    }
  }, [])

  return (
    <div className="auth-art" ref={stageRef} aria-hidden="true">
      <canvas className="auth-art-light" ref={lightRef} />
      <svg className="auth-emblem" viewBox="2 2 28 28" width="46" height="46">
        <use href="/favicon.svg#mark" />
      </svg>
    </div>
  )
}
