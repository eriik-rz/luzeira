// Arranque: conecta con Visoria (o con la API simulada), pide la lista de temas y abre
// el editor con el tema que está en la pantalla (o, si no se sabe, el primero de «Mis
// temas», o el primero incluido si no hay ninguno).

import { useEffect, useState } from 'react'
import { connect, errorMessage, type EditorApi, type Info, type ThemeSummary } from './api.ts'
import { Editor } from './Editor.tsx'
import { pickInitialTheme } from './model/contract.ts'
import { IconWarn } from './components/icons.tsx'

type Boot =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; api: EditorApi; info: Info | null; themes: ThemeSummary[] }

export default function App() {
  const [boot, setBoot] = useState<Boot>({ kind: 'loading' })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const api = await connect()
        const [info, themes] = await Promise.all([api.info().catch(() => null), api.listThemes()])
        if (alive) setBoot({ kind: 'ready', api, info, themes })
      } catch (e) {
        if (alive) setBoot({ kind: 'error', message: errorMessage(e) })
      }
    })()
    return () => {
      alive = false
    }
  }, [attempt])

  if (boot.kind === 'loading') {
    return (
      <div className="fullscreen-msg">
        <div className="card">
          <div className="spinner" />
          <p style={{ marginTop: 12 }}>Conectando con Visoria…</p>
        </div>
      </div>
    )
  }
  if (boot.kind === 'error') {
    return (
      <div className="fullscreen-msg">
        <div className="card">
          <IconWarn />
          <h1>No se puede abrir el editor</h1>
          <p>{boot.message}</p>
          <button
            type="button"
            className="btn primary"
            onClick={() => {
              setBoot({ kind: 'loading' })
              setAttempt((a) => a + 1)
            }}
          >
            Reintentar
          </button>
        </div>
      </div>
    )
  }
  const first = pickInitialTheme(boot.themes, boot.info?.active_theme)
  if (!first) {
    return (
      <div className="fullscreen-msg">
        <div className="card">
          <h1>No hay temas</h1>
          <p>Visoria no ha encontrado ningún tema. Reinstalar Visoria debería arreglarlo.</p>
        </div>
      </div>
    )
  }
  return <Editor api={boot.api} info={boot.info} initialThemes={boot.themes} initialName={first.name} />
}
