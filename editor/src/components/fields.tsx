// Campos del panel de propiedades. Todos llevan etiqueta, texto de ayuda (enlazado con
// aria-describedby) y, si hay, el error en español junto al campo.

import { useId, useState, type ReactNode } from 'react'

interface FieldProps {
  label: string
  hint?: string
  error?: string | null
  counter?: string
  children: (ids: { inputId: string; describedBy: string | undefined; invalid: boolean }) => ReactNode
}

export function Field({ label, hint, error, counter, children }: FieldProps) {
  const inputId = useId()
  const hintId = useId()
  const errId = useId()
  const describedBy = [hint ? hintId : null, error ? errId : null].filter(Boolean).join(' ') || undefined
  return (
    <div className="field">
      <label htmlFor={inputId}>
        <span>{label}</span>
        {counter && <span className="counter">{counter}</span>}
      </label>
      {children({ inputId, describedBy, invalid: Boolean(error) })}
      {error && (
        <p className="err" id={errId} role="alert">
          {error}
        </p>
      )}
      {hint && !error && (
        <p className="hint" id={hintId}>
          {hint}
        </p>
      )}
    </div>
  )
}

interface NumberFieldProps {
  label: string
  value: number
  onChange: (v: number) => void
  min?: number
  max?: number
  step?: number
  integer?: boolean
  hint?: string
  error?: string | null
  affix?: string // letra pequeña dentro del campo («X», «Y»…)
}

/**
 * Campo numérico. Mientras se escribe se guarda el texto tal cual; solo se aplica al
 * tema cuando es un número válido (y la coma decimal vale igual que el punto).
 */
export function NumberField({ label, value, onChange, min, max, step = 1, integer = true, hint, error, affix }: NumberFieldProps) {
  const [draft, setDraft] = useState<string | null>(null)
  const shown = draft ?? formatNumber(value)
  const parse = (text: string): number | null => {
    const n = Number(text.replace(',', '.').trim())
    if (text.trim() === '' || !Number.isFinite(n)) return null
    return integer ? Math.round(n) : n
  }
  const parsed = draft === null ? null : parse(draft)
  // Aviso al momento si lo escrito se sale del rango (el tema se queda con el límite).
  const localError =
    draft === null
      ? null
      : parsed === null
        ? 'Escribe un número.'
        : (min !== undefined && parsed < min) || (max !== undefined && parsed > max)
          ? rangeMessage(min, max)
          : null
  const input = (ids: { inputId: string; describedBy: string | undefined; invalid: boolean }) => (
    <input
      id={ids.inputId}
      className={`input num${ids.invalid ? ' invalid' : ''}`}
      inputMode={integer ? 'numeric' : 'decimal'}
      aria-describedby={ids.describedBy}
      aria-invalid={ids.invalid || undefined}
      value={shown}
      onChange={(e) => {
        setDraft(e.target.value)
        const n = parse(e.target.value)
        if (n !== null) onChange(n)
      }}
      onBlur={() => setDraft(null)}
      onKeyDown={(e) => {
        // Flechas arriba/abajo cambian el número (Mayús: de 10 en 10).
        if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return
        e.preventDefault()
        const delta = (e.key === 'ArrowUp' ? 1 : -1) * step * (e.shiftKey ? 10 : 1)
        let n = (parse(shown) ?? value) + delta
        if (min !== undefined) n = Math.max(min, n)
        if (max !== undefined) n = Math.min(max, n)
        n = integer ? Math.round(n) : Math.round(n * 100) / 100
        setDraft(null)
        onChange(n)
      }}
    />
  )
  return (
    <Field label={label} hint={hint} error={localError ?? error}>
      {(ids) =>
        affix ? (
          <div className="input-affix">
            <span className="affix" aria-hidden="true">
              {affix}
            </span>
            {input(ids)}
          </div>
        ) : (
          input(ids)
        )
      }
    </Field>
  )
}

function rangeMessage(min?: number, max?: number): string {
  if (min !== undefined && max !== undefined) return `Tiene que estar entre ${formatNumber(min)} y ${formatNumber(max)}.`
  if (max !== undefined) return `Como mucho ${formatNumber(max)}.`
  return `Como poco ${formatNumber(min ?? 0)}.`
}

function formatNumber(n: number): string {
  return String(n).replace('.', ',')
}

interface TextFieldProps {
  label: string
  value: string
  onChange: (v: string) => void
  maxLength: number
  hint?: string
  error?: string | null
  multiline?: boolean
  placeholder?: string
}

export function TextField({ label, value, onChange, maxLength, hint, error, multiline, placeholder }: TextFieldProps) {
  return (
    <Field label={label} hint={hint} error={error} counter={`${value.length}/${maxLength}`}>
      {({ inputId, describedBy, invalid }) =>
        multiline ? (
          <textarea
            id={inputId}
            className={`input${invalid ? ' invalid' : ''}`}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
            maxLength={maxLength}
            value={value}
            placeholder={placeholder}
            rows={2}
            onChange={(e) => onChange(e.target.value)}
          />
        ) : (
          <input
            id={inputId}
            className={`input${invalid ? ' invalid' : ''}`}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
            maxLength={maxLength}
            value={value}
            placeholder={placeholder}
            onChange={(e) => onChange(e.target.value)}
          />
        )
      }
    </Field>
  )
}

interface ColorFieldProps {
  label: string
  value: string
  onChange: (v: string) => void
  hint?: string
  error?: string | null
}

/** Selector de color del sistema + el código #rrggbb para quien lo quiera escribir. */
export function ColorField({ label, value, onChange, hint, error }: ColorFieldProps) {
  const [draft, setDraft] = useState<string | null>(null)
  const valid = (v: string) => /^#[0-9a-fA-F]{6}$/.test(v)
  return (
    <Field label={label} hint={hint} error={error}>
      {({ inputId, describedBy, invalid }) => (
        <div className="color-field">
          <input
            type="color"
            aria-label={`${label}: elegir en la paleta`}
            value={valid(value) ? value.toLowerCase() : '#000000'}
            onChange={(e) => {
              setDraft(null)
              onChange(e.target.value)
            }}
          />
          <input
            id={inputId}
            className={`input${invalid || (draft !== null && !valid(draft)) ? ' invalid' : ''}`}
            aria-describedby={describedBy}
            spellCheck={false}
            maxLength={7}
            value={draft ?? value}
            onChange={(e) => {
              let v = e.target.value.trim()
              if (v && !v.startsWith('#')) v = `#${v}`
              setDraft(v)
              if (valid(v)) onChange(v.toLowerCase())
            }}
            onBlur={() => setDraft(null)}
          />
        </div>
      )}
    </Field>
  )
}

interface ToggleProps {
  label: string
  checked: boolean
  onChange: (v: boolean) => void
  hint?: string
}

export function Toggle({ label, checked, onChange, hint }: ToggleProps) {
  const hintId = useId()
  return (
    <div className="field">
      <label className="toggle">
        <input
          type="checkbox"
          role="switch"
          checked={checked}
          aria-describedby={hint ? hintId : undefined}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span>{label}</span>
      </label>
      {hint && (
        <p className="hint" id={hintId}>
          {hint}
        </p>
      )}
    </div>
  )
}

interface SegmentedOption<T extends string> {
  value: T
  label: string
  icon?: ReactNode
  help?: string
}

interface SegmentedProps<T extends string> {
  label: string
  value: T
  options: SegmentedOption<T>[]
  onChange: (v: T) => void
  hint?: string
}

/** Grupo de opciones excluyentes (radiogroup accesible con flechas). */
export function Segmented<T extends string>({ label, value, options, onChange, hint }: SegmentedProps<T>) {
  const labelId = useId()
  const hintId = useId()
  const move = (dir: number) => {
    const i = options.findIndex((o) => o.value === value)
    const next = options[(i + dir + options.length) % options.length]
    onChange(next.value)
  }
  return (
    <div className="field">
      <span className="label" id={labelId}>
        {label}
      </span>
      <div
        className="segmented"
        role="radiogroup"
        aria-labelledby={labelId}
        aria-describedby={hint ? hintId : undefined}
        onKeyDown={(e) => {
          if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
            e.preventDefault()
            move(1)
          } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
            e.preventDefault()
            move(-1)
          }
        }}
      >
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={o.value === value}
            tabIndex={o.value === value ? 0 : -1}
            title={o.help ?? o.label}
            aria-label={o.icon ? o.label : undefined}
            onClick={() => onChange(o.value)}
          >
            {o.icon ?? o.label}
          </button>
        ))}
      </div>
      {hint && (
        <p className="hint" id={hintId}>
          {hint}
        </p>
      )}
    </div>
  )
}

interface SelectFieldProps<T extends string> {
  label: string
  value: T
  options: { value: T; label: string }[]
  onChange: (v: T) => void
  hint?: string
  error?: string | null
}

export function SelectField<T extends string>({ label, value, options, onChange, hint, error }: SelectFieldProps<T>) {
  return (
    <Field label={label} hint={hint} error={error}>
      {({ inputId, describedBy, invalid }) => (
        <select
          id={inputId}
          className={`input${invalid ? ' invalid' : ''}`}
          aria-describedby={describedBy}
          value={value}
          onChange={(e) => onChange(e.target.value as T)}
        >
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      )}
    </Field>
  )
}
