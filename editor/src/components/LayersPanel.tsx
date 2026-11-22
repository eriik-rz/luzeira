// Lista de capas. Se muestra como en Figma o Canva: ARRIBA = DELANTE. En el tema es al
// revés (el primero se pinta primero y queda al fondo), así que la lista va invertida.
// Reordenar: arrastrar la fila, o los botones de subir/bajar (y Alt+↑/↓ con el teclado).

import { useState, type Dispatch } from 'react'
import { gapToIndex, type Action } from '../model/editorState.ts'
import { MAX_WIDGETS, type Theme, type WidgetType } from '../model/types.ts'
import { widgetTypeInfo } from '../model/theme.ts'
import { useT } from '../i18n/contexto.ts'
import { IconDown, IconGrip, IconPlus, IconUp, WidgetIcon } from './icons.tsx'

interface Props {
  theme: Theme
  ids: string[]
  labels: string[]
  selectedId: string | null
  errorIds: Set<string>
  dispatch: Dispatch<Action>
  onAdd: (type: WidgetType) => void
}

const ADD_ORDER: WidgetType[] = ['text', 'clock', 'sensor', 'bar', 'image']

export function LayersPanel({ theme, ids, labels, selectedId, errorIds, dispatch, onAdd }: Props) {
  const t = useT()
  const [menuOpen, setMenuOpen] = useState(false)
  const [dragFrom, setDragFrom] = useState<number | null>(null) // índice en el tema
  const [gap, setGap] = useState<number | null>(null) // hueco en la lista mostrada
  const n = theme.widgets.length
  const full = n >= MAX_WIDGETS
  // Posiciones mostradas: d = 0 es el último del tema (el de delante).
  const order = Array.from({ length: n }, (_, d) => n - 1 - d)

  const moveBy = (index: number, dir: 1 | -1) => {
    // dir 1 = hacia delante (más arriba en la lista).
    dispatch({ type: 'reorder', from: index, to: index + dir })
  }

  const drop = () => {
    if (dragFrom !== null && gap !== null) {
      dispatch({ type: 'reorder', from: dragFrom, to: gapToIndex(n, dragFrom, gap) })
    }
    setDragFrom(null)
    setGap(null)
  }

  return (
    <aside className="left" aria-label={t('capas.titulo')}>
      <div className="panel-head">
        <h2 id="layers-title">{t('capas.titulo')}</h2>
        <div
          className="menu-wrap"
          onBlur={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setMenuOpen(false)
          }}
        >
          <button
            type="button"
            className="btn primary small"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            disabled={full}
            title={full ? t('capas.lleno', { max: MAX_WIDGETS }) : t('capas.anadir.ayuda')}
            onClick={() => setMenuOpen((o) => !o)}
          >
            <IconPlus /> {t('capas.anadir')}
          </button>
          {menuOpen && (
            <div
              className="menu"
              role="menu"
              aria-label={t('capas.tipo')}
              onKeyDown={(e) => {
                const items = [...e.currentTarget.querySelectorAll<HTMLButtonElement>('[role=menuitem]')]
                const i = items.indexOf(document.activeElement as HTMLButtonElement)
                if (e.key === 'ArrowDown') items[(i + 1) % items.length]?.focus()
                else if (e.key === 'ArrowUp') items[(i - 1 + items.length) % items.length]?.focus()
                else if (e.key === 'Escape') setMenuOpen(false)
                else return
                e.preventDefault()
              }}
            >
              {ADD_ORDER.map((type, i) => (
                <button
                  key={type}
                  type="button"
                  role="menuitem"
                  className="menu-item"
                  autoFocus={i === 0}
                  onClick={() => {
                    setMenuOpen(false)
                    onAdd(type)
                  }}
                >
                  <WidgetIcon type={type} />
                  <div>
                    <strong>{widgetTypeInfo(type).label}</strong>
                    <span>{widgetTypeInfo(type).help}</span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
      <p className="panel-hint">{t('capas.pista')}</p>

      {n === 0 ? (
        <div className="empty-layers">
          {t('capas.vacia')}
          <br />
          {t('capas.vacia.pista')}
        </div>
      ) : (
        <ul
          className="layers"
          aria-labelledby="layers-title"
          onDragOver={(e) => {
            if (dragFrom === null) return
            e.preventDefault()
            e.dataTransfer.dropEffect = 'move'
          }}
          onDrop={(e) => {
            e.preventDefault()
            drop()
          }}
        >
          {order.map((index, d) => {
            const id = ids[index]
            const w = theme.widgets[index]
            const selected = id === selectedId
            return (
              <li
                key={id}
                className={`layer${selected ? ' selected' : ''}${errorIds.has(id) ? ' has-error' : ''}${dragFrom === index ? ' dragging' : ''}`}
                draggable
                onDragStart={(e) => {
                  setDragFrom(index)
                  e.dataTransfer.effectAllowed = 'move'
                  e.dataTransfer.setData('text/plain', id)
                }}
                onDragOver={(e) => {
                  if (dragFrom === null) return
                  const r = e.currentTarget.getBoundingClientRect()
                  setGap(e.clientY < r.top + r.height / 2 ? d : d + 1)
                }}
                onDragEnd={() => {
                  setDragFrom(null)
                  setGap(null)
                }}
                onClick={() => dispatch({ type: 'select', id })}
              >
                {gap === d && dragFrom !== null && <span className="drop-line before" />}
                {gap === d + 1 && dragFrom !== null && d === n - 1 && <span className="drop-line after" />}
                <span className="grip" aria-hidden="true">
                  <IconGrip />
                </span>
                <span className="type-icon">
                  <WidgetIcon type={w.type} />
                </span>
                <button
                  type="button"
                  className="layer-button"
                  aria-pressed={selected}
                  aria-label={`${widgetTypeInfo(w.type).label}: ${labels[index]}${errorIds.has(id) ? ` ${t('capas.con_errores')}` : ''}`}
                  onKeyDown={(e) => {
                    if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
                      e.preventDefault()
                      e.stopPropagation()
                      moveBy(index, e.key === 'ArrowUp' ? 1 : -1)
                    }
                  }}
                  title={t('capas.alt')}
                >
                  <span className="layer-name">{labels[index]}</span>
                </button>
                {selected && (
                  <span className="layer-actions">
                    <button
                      type="button"
                      className="btn ghost small icon"
                      disabled={index === n - 1}
                      onClick={(e) => {
                        e.stopPropagation()
                        moveBy(index, 1)
                      }}
                      title={t('capas.delante.ayuda')}
                      aria-label={t('capas.delante')}
                    >
                      <IconUp />
                    </button>
                    <button
                      type="button"
                      className="btn ghost small icon"
                      disabled={index === 0}
                      onClick={(e) => {
                        e.stopPropagation()
                        moveBy(index, -1)
                      }}
                      title={t('capas.atras.ayuda')}
                      aria-label={t('capas.atras')}
                    >
                      <IconDown />
                    </button>
                  </span>
                )}
              </li>
            )
          })}
        </ul>
      )}
      <div className="layers-foot">
        {t('capas.cuenta', { n, max: MAX_WIDGETS })}
      </div>
    </aside>
  )
}
