// Selector de sensor con búsqueda, agrupado por categoría y con el valor en vivo.
// Combobox accesible: flechas para moverse, Intro para elegir, Esc para cerrar.

import { useEffect, useId, useMemo, useRef, useState } from 'react'
import type { SensorInfo } from '../api.ts'
import { categoryLabel, liveText } from './services.ts'
import { useT } from '../i18n/contexto.ts'

interface Props {
  label: string
  hint: string
  value: string
  sensors: SensorInfo[]
  kinds: SensorInfo['kind'][] // qué tipos de dato encajan con este elemento
  onChange: (id: string) => void
  error?: string | null
}

export function SensorPicker({ label, hint, value, sensors, kinds, onChange, error }: Props) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const wrapRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const listId = useId()
  const labelId = useId()
  const hintId = useId()

  const current = sensors.find((s) => s.id === value)
  const options = useMemo(() => {
    const q = normalize(query)
    return sensors
      .filter((s) => kinds.includes(s.kind))
      .filter((s) => !q || normalize(`${s.label} ${s.id} ${categoryLabel(s.category)}`).includes(q))
      .sort((a, b) => categoryRank(a.category) - categoryRank(b.category))
  }, [sensors, kinds, query])

  useEffect(() => {
    if (open) searchRef.current?.focus()
  }, [open])

  const choose = (s: SensorInfo) => {
    onChange(s.id)
    setOpen(false)
    setQuery('')
  }

  const live = liveText(current)
  return (
    <div className="field">
      <span className="label" id={labelId}>
        {label}
      </span>
      <div
        ref={wrapRef}
        style={{ position: 'relative' }}
        onBlur={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOpen(false)
        }}
      >
        <button
          type="button"
          className={`picker-btn${error ? ' input invalid' : ''}`}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-labelledby={labelId}
          aria-describedby={hintId}
          onClick={() => {
            setOpen((o) => !o)
            setActive(Math.max(0, options.findIndex((o) => o.id === value)))
          }}
        >
          <span className="main">
            <strong>{current ? current.label : value ? t('recursos.sensor.no_disponible') : t('recursos.sensor.elige')}</strong>
            <span>{value}</span>
          </span>
          <span className={`live${live === null ? ' none' : ''}`} title={current?.reason ?? undefined}>
            {live ?? (current ? t('recursos.sensor.sin_dato') : '')}
          </span>
        </button>
        {open && (
          <div className="picker-pop">
            <div className="search">
              <input
                ref={searchRef}
                className="input"
                placeholder={t('recursos.sensor.buscar')}
                aria-label={t('recursos.sensor.buscar.etiqueta')}
                role="combobox"
                aria-expanded="true"
                aria-controls={listId}
                aria-activedescendant={options[active] ? `${listId}-${options[active].id}` : undefined}
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value)
                  setActive(0)
                }}
                onKeyDown={(e) => {
                  if (e.key === 'ArrowDown') setActive((a) => Math.min(options.length - 1, a + 1))
                  else if (e.key === 'ArrowUp') setActive((a) => Math.max(0, a - 1))
                  else if (e.key === 'Enter' && options[active]) choose(options[active])
                  else if (e.key === 'Escape') setOpen(false)
                  else return
                  e.preventDefault()
                  e.stopPropagation()
                }}
              />
            </div>
            <ul className="picker-list" role="listbox" id={listId} aria-labelledby={labelId}>
              {options.length === 0 && <li className="picker-empty">{t('recursos.sensor.ninguno')}</li>}
              {options.map((s, i) => {
                const header = i === 0 || options[i - 1].category !== s.category
                const text = liveText(s)
                return (
                  <li key={s.id} role="presentation">
                    {header && (
                      <div className="picker-group" aria-hidden="true">
                        {categoryLabel(s.category)}
                      </div>
                    )}
                    <div
                      id={`${listId}-${s.id}`}
                      role="option"
                      aria-selected={s.id === value}
                      className={`picker-opt${i === active ? ' active' : ''}`}
                      onMouseEnter={() => setActive(i)}
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => choose(s)}
                      title={s.reason ?? s.id}
                    >
                      <span className="lbl">{s.label}</span>
                      {s.kind === 'text' && <span className="badge">{t('recursos.sensor.texto')}</span>}
                      <span className={`live${text === null ? ' none' : ''}`}>{text ?? t('recursos.sensor.sin_dato')}</span>
                    </div>
                  </li>
                )
              })}
            </ul>
          </div>
        )}
      </div>
      {error ? (
        <p className="err" role="alert">
          {error}
        </p>
      ) : (
        <p className="hint" id={hintId}>
          {current?.reason && current.value === null && current.text === null ? current.reason : hint}
        </p>
      )}
    </div>
  )
}

const ORDER = ['cpu', 'gpu', 'memory', 'disk', 'network', 'system']
const categoryRank = (c: string) => {
  const i = ORDER.indexOf(c)
  return i < 0 ? ORDER.length : i
}
const normalize = (s: string) =>
  s
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
