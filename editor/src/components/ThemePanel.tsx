// Propiedades del tema (se ven cuando no hay ningún elemento seleccionado).

import type { Dispatch } from 'react'
import type { Action } from '../model/editorState.ts'
import { ORIENTATION_INFO } from '../model/theme.ts'
import { LIMITS, type Orientation, type Theme } from '../model/types.ts'
import type { Issue } from '../model/validate.ts'
import { ColorField, Field, TextField } from './fields.tsx'
import { ImagePicker } from './ResourcePicker.tsx'
import type { Services } from './services.ts'

interface Props {
  theme: Theme
  issues: Issue[]
  services: Services
  dispatch: Dispatch<Action>
}

const ORIENTATIONS: Orientation[] = ['landscape', 'portrait', 'reverse-landscape', 'reverse-portrait']

export function ThemePanel({ theme, issues, services, dispatch }: Props) {
  const err = (path: string) => issues.find((i) => i.path === path)?.message ?? null
  const general = issues.filter((i) => i.path === '' || i.path === 'widgets')
  const patch = (p: Parameters<typeof makePatch>[0], field: string) =>
    dispatch({ type: 'updateTheme', patch: makePatch(p), mergeKey: `theme:${field}`, at: Date.now() })

  return (
    <>
      <div className="props-title">
        <div>
          <h2>Tema</h2>
          <p>Haz clic en un elemento del lienzo para editarlo.</p>
        </div>
      </div>
      <div className="panel-body">
        {general.map((g) => (
          <p key={g.message} className="err" role="alert">
            {g.message}
          </p>
        ))}
        <section className="section">
          <h3>Datos</h3>
          <TextField label="Nombre" value={theme.name} maxLength={LIMITS.nameMax} onChange={(name) => patch({ name }, 'name')} hint="Así aparece en la lista de temas." error={err('name')} />
          <TextField
            label="Autor (opcional)"
            value={theme.author ?? ''}
            maxLength={LIMITS.authorMax}
            onChange={(a) => patch({ author: a === '' ? null : a }, 'author')}
            hint="Si compartes el tema, quién lo hizo. Puede ser un apodo."
            error={err('author')}
          />
        </section>

        <section className="section">
          <h3>Pantalla</h3>
          <Field label="Orientación" hint="Cómo tienes colocada la pantalla. Si al girarla algo no cabe, se recoloca dentro.">
            {({ inputId, describedBy }) => (
              <select
                id={inputId}
                className="input"
                aria-describedby={describedBy}
                value={theme.orientation}
                onChange={(e) => dispatch({ type: 'setOrientation', orientation: e.target.value as Orientation })}
              >
                {ORIENTATIONS.map((o) => (
                  <option key={o} value={o}>
                    {ORIENTATION_INFO[o].label} — {ORIENTATION_INFO[o].help}
                  </option>
                ))}
              </select>
            )}
          </Field>
        </section>

        <section className="section">
          <h3>Fondo</h3>
          <ColorField
            label="Color de fondo"
            value={theme.background.color}
            onChange={(color) => patch({ background: { ...theme.background, color } }, 'bg-color')}
            hint="Se ve donde no hay imagen de fondo."
            error={err('background.color')}
          />
          <ImagePicker
            label="Imagen de fondo"
            hint="Llena toda la pantalla; si no tiene la misma forma, se recorta por los lados."
            value={theme.background.image}
            services={services}
            allowNone
            noneLabel="Solo color"
            onChange={(image) => patch({ background: { ...theme.background, image } }, 'bg-image')}
            error={err('background.image')}
          />
        </section>
      </div>
    </>
  )
}

function makePatch(p: Partial<Pick<Theme, 'name' | 'author' | 'background'>>) {
  return p
}
