// Propiedades del tema (se ven cuando no hay ningún elemento seleccionado).

import type { Dispatch } from 'react'
import type { Action } from '../model/editorState.ts'
import { orientationInfo } from '../model/theme.ts'
import { useT } from '../i18n/contexto.ts'
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
  const t = useT()
  const err = (path: string) => issues.find((i) => i.path === path)?.message ?? null
  const general = issues.filter((i) => i.path === '' || i.path === 'widgets')
  const patch = (p: Parameters<typeof makePatch>[0], field: string) =>
    dispatch({ type: 'updateTheme', patch: makePatch(p), mergeKey: `theme:${field}`, at: Date.now() })

  return (
    <>
      <div className="props-title">
        <div>
          <h2>{t('tema.titulo')}</h2>
          <p>{t('tema.pista')}</p>
        </div>
      </div>
      <div className="panel-body">
        {general.map((g) => (
          <p key={g.message} className="err" role="alert">
            {g.message}
          </p>
        ))}
        <section className="section">
          <h3>{t('tema.datos')}</h3>
          <TextField label={t('tema.nombre')} value={theme.name} maxLength={LIMITS.nameMax} onChange={(name) => patch({ name }, 'name')} hint={t('tema.nombre.ayuda')} error={err('name')} />
          <TextField
            label={t('tema.autor')}
            value={theme.author ?? ''}
            maxLength={LIMITS.authorMax}
            onChange={(a) => patch({ author: a === '' ? null : a }, 'author')}
            hint={t('tema.autor.ayuda')}
            error={err('author')}
          />
        </section>

        <section className="section">
          <h3>{t('tema.pantalla')}</h3>
          <Field label={t('tema.orientacion')} hint={t('tema.orientacion.ayuda')}>
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
                    {orientationInfo(o).label} — {orientationInfo(o).help}
                  </option>
                ))}
              </select>
            )}
          </Field>
        </section>

        <section className="section">
          <h3>{t('tema.fondo')}</h3>
          <ColorField
            label={t('tema.fondo.color')}
            value={theme.background.color}
            onChange={(color) => patch({ background: { ...theme.background, color } }, 'bg-color')}
            hint={t('tema.fondo.color.ayuda')}
            error={err('background.color')}
          />
          <ImagePicker
            label={t('tema.fondo.imagen')}
            hint={t('tema.fondo.imagen.ayuda')}
            value={theme.background.image}
            services={services}
            allowNone
            noneLabel={t('tema.fondo.solo_color')}
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
