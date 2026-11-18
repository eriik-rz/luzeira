// Lienzo: la vista previa PNG que pinta Python (la misma imagen que irá a la pantalla)
// ampliada sin suavizar, y encima un recuadro por elemento para seleccionar, mover y
// redimensionar. Los recuadros no pintan nada: lo que se ve es siempre la imagen real.

import { useEffect, useRef, useState, type Dispatch, type PointerEvent as ReactPointerEvent } from 'react'
import { HANDLES, HANDLE_CURSOR, GRID, fitZoom, moveBox, pickBelow, resizeBox, type Handle, type ZoomMode } from '../model/geometry.ts'
export type { ZoomMode }
import type { Action } from '../model/editorState.ts'
import { screenSize, type Box, type Theme } from '../model/types.ts'
import { WIDGET_TYPE_INFO } from '../model/theme.ts'

interface Props {
  theme: Theme
  ids: string[]
  selectedId: string | null
  zoomMode: ZoomMode
  snap: boolean
  showOutlines: boolean
  previewUrl: string | null
  previewStale: boolean
  errorIds: Set<string>
  labels: string[]
  dispatch: Dispatch<Action>
  onPointerPos: (p: { x: number; y: number } | null) => void
  /** Fichero soltado sobre el lienzo, con el punto de la pantalla donde cayó. */
  onDropFile: (file: File, point: { x: number; y: number }) => void
}

interface Drag {
  id: string
  mode: 'move' | Handle
  start: Box
  startX: number
  startY: number
  key: string
  pointerId: number
  moved: boolean
}

let gestureCounter = 0

/** ¿Lo que se arrastra trae ficheros (y no, p. ej., una fila de la lista de capas)? */
function hasFiles(dt: DataTransfer): boolean {
  return Array.from(dt.types).includes('Files')
}


export function CanvasArea(props: Props) {
  const { theme, ids, selectedId, zoomMode, snap, showOutlines, previewUrl, previewStale, errorIds, labels, dispatch, onPointerPos, onDropFile } = props
  const screen = screenSize(theme)
  const stageRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<Drag | null>(null)
  const [dragging, setDragging] = useState<string | null>(null)
  const [fileOver, setFileOver] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [room, setRoom] = useState({ width: 960, height: 640 })
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setRoom({ width: el.clientWidth, height: el.clientHeight }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const zoom = zoomMode === 'fit' ? fitZoom(screen, room) : zoomMode

  const toScreen = (e: { clientX: number; clientY: number }) => {
    const r = stageRef.current?.getBoundingClientRect()
    if (!r) return null
    return {
      x: Math.floor((e.clientX - r.left) / zoom),
      y: Math.floor((e.clientY - r.top) / zoom),
    }
  }

  const begin = (e: ReactPointerEvent, id: string, mode: Drag['mode'], start: Box) => {
    if (e.button !== 0) return
    e.stopPropagation()
    e.preventDefault()
    if (mode === 'move' && e.altKey) {
      // Alt+clic: la capa de debajo (ver pickBelow).
      const p = toScreen(e)
      const below = p && pickBelow(theme.widgets.map((w) => w.box), ids, p, selectedId)
      if (below) {
        id = below
        start = theme.widgets[ids.indexOf(below)].box
      }
    }
    dispatch({ type: 'select', id })
    try {
      ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    } catch {
      // Sin captura el arrastre sigue funcionando mientras el ratón esté sobre el lienzo.
    }
    dragRef.current = {
      id,
      mode,
      start,
      startX: e.clientX,
      startY: e.clientY,
      key: `gesture:${++gestureCounter}`,
      pointerId: e.pointerId,
      moved: false,
    }
  }

  const onMove = (e: ReactPointerEvent) => {
    onPointerPos(toScreen(e))
    const d = dragRef.current
    if (!d || d.pointerId !== e.pointerId) return
    const dx = (e.clientX - d.startX) / zoom
    const dy = (e.clientY - d.startY) / zoom
    // Un umbral pequeño evita mover sin querer al hacer clic para seleccionar.
    if (!d.moved && Math.hypot(dx, dy) * zoom < 3) return
    if (!d.moved) {
      d.moved = true
      setDragging(d.id)
    }
    const useSnap = snap && !e.altKey // Alt: mover sin imán
    const box =
      d.mode === 'move'
        ? moveBox(d.start, dx, dy, screen, useSnap)
        : resizeBox(d.start, d.mode, dx, dy, screen, useSnap)
    dispatch({ type: 'setBox', id: d.id, box, mergeKey: d.key, at: Date.now() })
  }

  const end = (e: ReactPointerEvent) => {
    const d = dragRef.current
    if (!d || d.pointerId !== e.pointerId) return
    dragRef.current = null
    setDragging(null)
  }

  const selIndex = selectedId ? ids.indexOf(selectedId) : -1
  const sel = selIndex >= 0 ? theme.widgets[selIndex] : null
  const gridPx = GRID * zoom

  return (
    <div
      ref={scrollRef}
      className="canvas-scroll"
      onPointerDown={(e) => {
        if (e.target === e.currentTarget || (e.target as HTMLElement).classList.contains('canvas-inner')) {
          dispatch({ type: 'select', id: null })
        }
      }}
    >
      <div className="canvas-inner">
        <div
          ref={stageRef}
          className={`stage${showOutlines ? ' show-outlines' : ''}${fileOver ? ' file-over' : ''}`}
          style={{ width: screen.width * zoom, height: screen.height * zoom }}
          onPointerMove={onMove}
          onPointerUp={end}
          onPointerCancel={end}
          onPointerLeave={() => onPointerPos(null)}
          onDragOver={(e) => {
            if (!hasFiles(e.dataTransfer)) return
            e.preventDefault()
            e.dataTransfer.dropEffect = 'copy'
            if (!fileOver) setFileOver(true)
          }}
          onDragLeave={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFileOver(false)
          }}
          onDrop={(e) => {
            setFileOver(false)
            const file = e.dataTransfer.files[0]
            if (!file) return
            e.preventDefault()
            const p = toScreen(e)
            const point = p
              ? { x: Math.max(0, Math.min(screen.width, p.x)), y: Math.max(0, Math.min(screen.height, p.y)) }
              : { x: screen.width / 2, y: screen.height / 2 }
            onDropFile(file, point)
          }}
          onPointerDown={(e) => {
            if (e.target === e.currentTarget || (e.target as HTMLElement).classList.contains('stage-grid')) {
              dispatch({ type: 'select', id: null })
            }
          }}
          aria-label={`Lienzo de ${screen.width} por ${screen.height} píxeles`}
          role="group"
        >
          <div className="screen-label">
            {screen.width} × {screen.height} · zoom ×{String(zoom).replace('.', ',')}
          </div>
          {previewUrl ? (
            <img
              className={`stage-img${previewStale ? ' stale' : ''}`}
              src={previewUrl}
              alt="Vista previa de la pantalla"
              draggable={false}
            />
          ) : (
            <div className="stage-placeholder">Preparando la vista previa…</div>
          )}
          {snap && (
            <div
              className="stage-grid"
              aria-hidden="true"
              style={{
                backgroundImage: `linear-gradient(to right, rgba(255,255,255,0.05) 1px, transparent 1px),
                  linear-gradient(to bottom, rgba(255,255,255,0.05) 1px, transparent 1px),
                  linear-gradient(to right, rgba(255,255,255,0.08) 1px, transparent 1px),
                  linear-gradient(to bottom, rgba(255,255,255,0.08) 1px, transparent 1px)`,
                backgroundSize: `${gridPx}px ${gridPx}px, ${gridPx}px ${gridPx}px, ${gridPx * 4}px ${gridPx * 4}px, ${gridPx * 4}px ${gridPx * 4}px`,
              }}
            />
          )}
          {theme.widgets.map((w, i) => {
            const id = ids[i]
            const b = w.box
            return (
              <div
                key={id}
                className={`wbox${errorIds.has(id) ? ' error' : ''}${dragging === id ? ' ghost' : ''}`}
                style={{ left: b.x * zoom, top: b.y * zoom, width: b.width * zoom, height: b.height * zoom }}
                title={`${WIDGET_TYPE_INFO[w.type].label}: ${labels[i]}`}
                onPointerDown={(e) => begin(e, id, 'move', b)}
              />
            )
          })}
          {sel && selectedId && (
            <div
              className="selection"
              style={{ left: sel.box.x * zoom, top: sel.box.y * zoom, width: sel.box.width * zoom, height: sel.box.height * zoom }}
            >
              <div className={`box-tag${sel.box.y * zoom < 30 ? ' below' : ''}`}>
                {sel.box.x}, {sel.box.y} · {sel.box.width} × {sel.box.height}
              </div>
              {HANDLES.map((h) => (
                <div
                  key={h}
                  className="handle"
                  style={{
                    left: h.includes('w') ? 0 : h.includes('e') ? '100%' : '50%',
                    top: h.includes('n') ? 0 : h.includes('s') ? '100%' : '50%',
                    cursor: HANDLE_CURSOR[h],
                  }}
                  onPointerDown={(e) => begin(e, selectedId, h, sel.box)}
                  aria-hidden="true"
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
