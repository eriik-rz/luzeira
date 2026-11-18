// Elegir una imagen del tema (miniaturas) o subir una nueva; y lo mismo con las fuentes.
// Las miniaturas se piden por la API (llevan el token en la cabecera), por eso no se
// usa un <img src="/api/…"> directo: esa petición no llevaría la cabecera.

import { useEffect, useId, useState } from 'react'
import type { EditorApi } from '../api.ts'
import { baseName, isFontPath, isImagePath } from '../model/theme.ts'
import { IconUpload } from './icons.tsx'
import type { Services } from './services.ts'

function Thumb({ api, theme, path }: { api: EditorApi; theme: string; path: string }) {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    let created: string | null = null
    api.resource(theme, path, controller.signal).then(
      (blob) => {
        created = URL.createObjectURL(blob)
        setUrl(created)
      },
      () => setUrl(null),
    )
    return () => {
      controller.abort()
      if (created) URL.revokeObjectURL(created)
    }
  }, [api, theme, path])
  return url ? <img src={url} alt="" /> : <span aria-hidden="true">…</span>
}

interface ImageProps {
  label: string
  hint: string
  value: string | null
  services: Services
  allowNone?: boolean
  noneLabel?: string
  onChange: (path: string | null) => void
  error?: string | null
}

export function ImagePicker({ label, hint, value, services, allowNone, noneLabel = 'Sin imagen', onChange, error }: ImageProps) {
  const labelId = useId()
  const images = services.files.filter(isImagePath)
  return (
    <div className="field">
      <span className="label" id={labelId}>
        {label}
      </span>
      <div className="thumbs" role="group" aria-labelledby={labelId}>
        {allowNone && (
          <button type="button" className="thumb none" aria-pressed={value === null} onClick={() => onChange(null)}>
            {noneLabel}
          </button>
        )}
        {images.map((p) => (
          <button
            key={p}
            type="button"
            className="thumb"
            aria-pressed={value === p}
            title={baseName(p)}
            aria-label={`Usar ${baseName(p)}`}
            onClick={() => onChange(p)}
          >
            <Thumb api={services.api} theme={services.themeName} path={p} />
          </button>
        ))}
      </div>
      {value && <span className="thumb-name">{baseName(value)}</span>}
      <button
        type="button"
        className="btn small"
        onClick={async () => {
          const r = await services.upload('image')
          if (r) onChange(r.path)
        }}
        title="PNG, JPG o GIF de hasta 10 MB y 4096 píxeles de lado"
      >
        <IconUpload /> Subir imagen…
      </button>
      {error ? (
        <p className="err" role="alert">
          {error}
        </p>
      ) : (
        <p className="hint">{hint}</p>
      )}
    </div>
  )
}

interface FontProps {
  value: string | null
  services: Services
  onChange: (path: string | null) => void
  error?: string | null
}

export function FontPicker({ value, services, onChange, error }: FontProps) {
  const id = useId()
  const fonts = services.files.filter(isFontPath)
  return (
    <div className="field">
      <label htmlFor={id}>Tipo de letra</label>
      <div style={{ display: 'flex', gap: 6 }}>
        <select
          id={id}
          className={`input${error ? ' invalid' : ''}`}
          value={value ?? ''}
          onChange={(e) => onChange(e.target.value || null)}
        >
          <option value="">La de Luzeira</option>
          {fonts.map((f) => (
            <option key={f} value={f}>
              {baseName(f)}
            </option>
          ))}
          {value && !fonts.includes(value) && <option value={value}>{baseName(value)} (no está)</option>}
        </select>
        <button
          type="button"
          className="btn icon"
          title="Subir un tipo de letra (TTF u OTF)"
          aria-label="Subir un tipo de letra"
          onClick={async () => {
            const r = await services.upload('font')
            if (r) onChange(r.path)
          }}
        >
          <IconUpload />
        </button>
      </div>
      {error ? (
        <p className="err" role="alert">
          {error}
        </p>
      ) : (
        <p className="hint">La de Luzeira tiene tildes y ñ. Puedes subir la tuya (.ttf u .otf).</p>
      )}
    </div>
  )
}
