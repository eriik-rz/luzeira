// Diálogo modal sencillo: atrapa el foco, Esc cierra, el clic fuera también.

import { useEffect, useId, useRef, type ReactNode } from 'react'
import { useT } from '../i18n/contexto.ts'

interface Props {
  title: string
  children: ReactNode
  actions: ReactNode
  onClose: () => void
  /** Clase extra para la caja (p. ej. `settings`, más ancha). */
  className?: string
}

export function Dialog({ title, children, actions, onClose, className }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const titleId = useId()

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const el = ref.current
    // Foco al primer campo, o al botón principal si no hay campos.
    const first = el?.querySelector<HTMLElement>('input, textarea, select') ?? el?.querySelector<HTMLElement>('.btn.primary, .btn.solid, button')
    first?.focus()
    return () => previous?.focus()
  }, [])

  return (
    <div className="overlay" onPointerDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={ref}
        className={className ? `dialog ${className}` : 'dialog'}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Escape') {
            e.preventDefault()
            onClose()
          }
          if (e.key === 'Tab') {
            const items = [...(ref.current?.querySelectorAll<HTMLElement>('button, input, select, textarea') ?? [])].filter((x) => !x.hasAttribute('disabled'))
            if (items.length === 0) return
            const first = items[0]
            const last = items[items.length - 1]
            if (e.shiftKey && document.activeElement === first) {
              e.preventDefault()
              last.focus()
            } else if (!e.shiftKey && document.activeElement === last) {
              e.preventDefault()
              first.focus()
            }
          }
        }}
      >
        <h2 id={titleId}>{title}</h2>
        {children}
        <div className="actions">{actions}</div>
      </div>
    </div>
  )
}

export interface ToastItem {
  id: number
  message: string
  kind?: 'ok' | 'error' | 'info'
  action?: { label: string; run: () => void }
}

export function Toasts({ items, onDismiss }: { items: ToastItem[]; onDismiss: (id: number) => void }) {
  const tr = useT()
  return (
    <div className="toasts" role="status" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`toast ${t.kind ?? 'info'}`}>
          <span>{t.message}</span>
          {t.action && (
            <button
              type="button"
              className="btn small"
              onClick={() => {
                t.action?.run()
                onDismiss(t.id)
              }}
            >
              {t.action.label}
            </button>
          )}
          <button type="button" className="btn ghost small icon" aria-label={tr('comun.cerrar_aviso')} onClick={() => onDismiss(t.id)}>
            ×
          </button>
        </div>
      ))}
    </div>
  )
}
