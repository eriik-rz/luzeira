// Barra superior: tema actual, deshacer/rehacer, estado del guardado, «Guardar como…»
// «Aplicar a la pantalla» y «Ajustes» (lo del menú del icono, a la vista).

import type { Info, ThemeSummary } from '../api.ts'
import { displayPill } from '../model/contract.ts'
import { t } from '../model/i18n.ts'
import { useT } from '../i18n/contexto.ts'
import { BrandMark, IconRedo, IconSaveAs, IconSend, IconSettings, IconTrash, IconUndo } from './icons.tsx'

export type SaveStatus =
  | { kind: 'saved' }
  | { kind: 'pending' }
  | { kind: 'saving' }
  | { kind: 'invalid' }
  | { kind: 'error'; message: string }
  | { kind: 'readonly' }
  | { kind: 'readonly-dirty' }

interface Props {
  info: Info | null
  themes: ThemeSummary[]
  current: string
  builtin: boolean
  canUndo: boolean
  canRedo: boolean
  status: SaveStatus
  applying: boolean
  onSelectTheme: (name: string) => void
  onUndo: () => void
  onRedo: () => void
  onSaveAs: () => void
  onApply: () => void
  onSave: () => void
  onRestore: () => void
  canRestore: boolean
  onDeleteTheme: () => void
  onSettings: () => void
}

function statusView(s: SaveStatus): { cls: string; text: string; title: string } {
  switch (s.kind) {
    case 'saved':
      return { cls: 'ok', text: t('barra.estado.guardado'), title: t('barra.estado.guardado.ayuda') }
    case 'pending':
    case 'saving':
      return { cls: 'busy', text: t('barra.estado.guardando'), title: t('barra.estado.guardando.ayuda') }
    case 'invalid':
      return { cls: 'warn', text: t('barra.estado.errores'), title: t('barra.estado.errores.ayuda') }
    case 'error':
      return { cls: 'error', text: t('barra.estado.fallo'), title: s.message }
    case 'readonly':
      return { cls: '', text: t('barra.estado.incluido'), title: t('barra.estado.incluido.ayuda') }
    case 'readonly-dirty':
      return { cls: 'warn', text: t('barra.estado.sin_guardar'), title: t('barra.estado.sin_guardar.ayuda') }
  }
}

export function TopBar(p: Props) {
  useT()
  const st = statusView(p.status)
  const pill = displayPill(p.info && { status: p.info.status, message: p.info.message })
  const mine = p.themes.filter((t) => !t.builtin)
  const builtins = p.themes.filter((t) => t.builtin)
  return (
    <header className="topbar">
      <div className="brand" title={p.info && !p.info.simulated ? `Luzeira ${p.info.version}` : undefined}>
        <BrandMark />
        <span className="brand-text">
          Luzeira{' '}
          {p.info && (
            <span className="brand-version">{p.info.simulated ? t('barra.demo') : p.info.version}</span>
          )}{' '}
          <small>· {t('barra.editor')}</small>
        </span>
      </div>

      <label className="sr-only" htmlFor="theme-select">
        {t('barra.tema')}
      </label>
      <select
        id="theme-select"
        className="input"
        style={{ width: 230, minWidth: 150, flex: "0 4 230px" }}
        value={p.current}
        title={t('barra.tema.ayuda')}
        onChange={(e) => p.onSelectTheme(e.target.value)}
      >
        {mine.length > 0 && (
          <optgroup label={t('comun.mis_temas')}>
            {mine.map((t) => (
              <option key={t.name} value={t.name}>
                {t.title}
              </option>
            ))}
          </optgroup>
        )}
        <optgroup label={t('barra.incluidos')}>
          {builtins.map((t) => (
            <option key={t.name} value={t.name}>
              {t.title}
            </option>
          ))}
        </optgroup>
      </select>

      <button
        type="button"
        className="btn ghost danger"
        aria-label={t('barra.borrar.etiqueta')}
        disabled={p.builtin}
        onClick={p.onDeleteTheme}
        title={
          p.builtin
            ? t('barra.borrar.incluido')
            : t('barra.borrar.ayuda')
        }
      >
        <IconTrash /> <span className="btn-label">{t('comun.borrar')}</span>
      </button>

      <span className="sep" />
      <button type="button" className="btn ghost icon" disabled={!p.canUndo} onClick={p.onUndo} title={t('barra.deshacer.ayuda')} aria-label={t('comun.deshacer')}>
        <IconUndo />
      </button>
      <button type="button" className="btn ghost icon" disabled={!p.canRedo} onClick={p.onRedo} title={t('barra.rehacer.ayuda')} aria-label={t('barra.rehacer')}>
        <IconRedo />
      </button>
      <span className={`status-pill ${st.cls}`} title={st.title} role="status" aria-live="polite">
        <span className="dot" aria-hidden="true" />
        {st.text}
      </span>
      {p.info?.simulated && (
        <span
          className="status-pill sim"
          title={`${p.info.message} ${t('barra.simulado.ayuda')}`}
        >
          {t('barra.simulado')}
        </span>
      )}

      <span className="spacer" />
      {pill && pill.kind === 'note' && (
        <span className="status-pill note hide-narrow" title={pill.title}>
          {pill.text}
        </span>
      )}
      {pill && pill.kind !== 'note' && (
        <button
          type="button"
          className={`status-pill display-alert ${pill.kind}`}
          title={`${pill.title} ${t('barra.pastilla.ayuda')}`}
          onClick={p.onSettings}
          role="status"
          aria-live="polite"
        >
          <span className="dot" aria-hidden="true" />
          {pill.text}
        </button>
      )}
      <button
        type="button"
        className="btn ghost"
        onClick={p.onRestore}
        aria-label={t('comun.restaurar')}
        disabled={!p.canRestore}
        title={t('barra.restaurar.ayuda')}
      >
        <IconUndo /> <span className="btn-label">{t('comun.restaurar')}</span>
      </button>
      {p.builtin ? (
        <button
          type="button"
          className="btn primary highlight"
          aria-label={t('comun.guardar_como')}
          onClick={p.onSaveAs}
          title={t('barra.guardar_como.incluido')}
        >
          <IconSaveAs /> <span className="btn-label">{t('comun.guardar_como')}</span>
        </button>
      ) : (
        <>
          <button type="button" className="btn ghost" aria-label={t('comun.guardar_como')} onClick={p.onSaveAs} title={t('barra.guardar_como.ayuda')}>
            <IconSaveAs /> <span className="btn-label">{t('comun.guardar_como')}</span>
          </button>
          <button
            type="button"
            className="btn"
            aria-label={t('barra.guardar')}
            onClick={p.onSave}
            disabled={p.status.kind === 'saved'}
            title={p.status.kind === 'saved' ? t('barra.guardar.todo') : t('barra.guardar.ahora')}
          >
            <IconSaveAs /> <span className="btn-label">{t('barra.guardar')}</span>
          </button>
        </>
      )}
      <button
        type="button"
        className={`btn keep-label${p.builtin ? '' : ' primary'}`}
        onClick={p.onApply}
        disabled={p.applying}
        aria-label={t('barra.aplicar')}
        title={t('barra.aplicar.ayuda')}
      >
        <IconSend /> <span className="btn-label">{p.applying ? t('barra.aplicando') : t('barra.aplicar')}</span>
      </button>
      <button
        type="button"
        className="btn ghost"
        aria-label={t('ajustes.titulo')}
        onClick={p.onSettings}
        title={t('barra.ajustes.ayuda')}
      >
        <IconSettings /> <span className="btn-label">{t('ajustes.titulo')}</span>
      </button>
    </header>
  )
}
