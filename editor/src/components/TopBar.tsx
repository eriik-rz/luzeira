// Barra superior: tema actual, deshacer/rehacer, estado del guardado, «Guardar como…»
// «Aplicar a la pantalla» y «Ajustes» (lo del menú del icono, a la vista).

import type { Info, ThemeSummary } from '../api.ts'
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
      return { cls: 'ok', text: 'Guardado', title: 'Todos los cambios están guardados.' }
    case 'pending':
    case 'saving':
      return { cls: 'busy', text: 'Guardando…', title: 'Se guarda solo un momento después de cada cambio.' }
    case 'invalid':
      return { cls: 'warn', text: 'Sin guardar: hay errores', title: 'Corrige los campos marcados en rojo y se guardará solo.' }
    case 'error':
      return { cls: 'error', text: 'No se ha guardado', title: s.message }
    case 'readonly':
      return { cls: '', text: 'Tema incluido', title: 'Los temas incluidos no se pueden cambiar. Usa «Guardar como…» para tener tu copia.' }
    case 'readonly-dirty':
      return { cls: 'warn', text: 'Cambios sin guardar', title: 'Este tema es incluido: tus cambios solo se conservan con «Guardar como…».' }
  }
}

export function TopBar(p: Props) {
  const st = statusView(p.status)
  const mine = p.themes.filter((t) => !t.builtin)
  const builtins = p.themes.filter((t) => t.builtin)
  return (
    <header className="topbar">
      <div className="brand">
        <BrandMark />
        <span className="brand-text">
          Luzeira <small>· Editor de temas</small>
        </span>
      </div>

      <label className="sr-only" htmlFor="theme-select">
        Tema que estás editando
      </label>
      <select
        id="theme-select"
        className="input"
        style={{ width: 230, minWidth: 150, flex: "0 1 230px" }}
        value={p.current}
        title="Tema que estás editando. Cambia para abrir otro."
        onChange={(e) => p.onSelectTheme(e.target.value)}
      >
        {mine.length > 0 && (
          <optgroup label="Mis temas">
            {mine.map((t) => (
              <option key={t.name} value={t.name}>
                {t.title}
              </option>
            ))}
          </optgroup>
        )}
        <optgroup label="Incluidos con Luzeira">
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
        aria-label="Borrar tema"
        disabled={p.builtin}
        onClick={p.onDeleteTheme}
        title={
          p.builtin
            ? 'Los temas incluidos no se pueden borrar.'
            : 'Mover este tema a la papelera de Luzeira (se puede recuperar).'
        }
      >
        <IconTrash /> <span className="btn-label">Borrar</span>
      </button>

      <span className="sep" />
      <button type="button" className="btn ghost icon" disabled={!p.canUndo} onClick={p.onUndo} title="Deshacer (Ctrl+Z)" aria-label="Deshacer">
        <IconUndo />
      </button>
      <button type="button" className="btn ghost icon" disabled={!p.canRedo} onClick={p.onRedo} title="Rehacer (Ctrl+Y)" aria-label="Rehacer">
        <IconRedo />
      </button>
      <span className={`status-pill ${st.cls}`} title={st.title} role="status" aria-live="polite">
        <span className="dot" aria-hidden="true" />
        {st.text}
      </span>
      {p.info?.simulated && (
        <span className="status-pill sim" title={p.info.message}>
          Modo simulado
        </span>
      )}

      <span className="spacer" />
      {p.info && !p.info.simulated && p.info.message && (
        <span className="status-pill hide-narrow" title={p.info.message}>
          {p.info.message}
        </span>
      )}
      <button
        type="button"
        className="btn ghost"
        onClick={p.onRestore}
        aria-label="Restaurar"
        disabled={!p.canRestore}
        title="Volver a como estaba el tema al abrirlo (se puede deshacer con Ctrl+Z)"
      >
        <IconUndo /> <span className="btn-label">Restaurar</span>
      </button>
      {p.builtin ? (
        <button
          type="button"
          className="btn primary highlight"
          aria-label="Guardar como…"
          onClick={p.onSaveAs}
          title="Los temas incluidos no se cambian: guarda una copia tuya en «Mis temas»"
        >
          <IconSaveAs /> <span className="btn-label">Guardar como…</span>
        </button>
      ) : (
        <>
          <button type="button" className="btn ghost" aria-label="Guardar como…" onClick={p.onSaveAs} title="Guardar una copia con otro nombre en «Mis temas»">
            <IconSaveAs /> <span className="btn-label">Guardar como…</span>
          </button>
          <button
            type="button"
            className="btn"
            onClick={p.onSave}
            disabled={p.status.kind === 'saved'}
            title={p.status.kind === 'saved' ? 'Todo está guardado (se guarda solo al cambiar algo).' : 'Guardar ahora'}
          >
            <IconSaveAs /> Guardar
          </button>
        </>
      )}
      <button
        type="button"
        className={`btn${p.builtin ? '' : ' primary'}`}
        onClick={p.onApply}
        disabled={p.applying}
        title="Mostrar este tema en la pantalla USB ahora y dejarlo puesto"
      >
        <IconSend /> {p.applying ? 'Aplicando…' : 'Aplicar a la pantalla'}
      </button>
      <button
        type="button"
        className="btn ghost"
        aria-label="Ajustes"
        onClick={p.onSettings}
        title="Brillo, girar, elegir pantalla, iniciar con Windows e importar temas"
      >
        <IconSettings /> <span className="btn-label">Ajustes</span>
      </button>
    </header>
  )
}
