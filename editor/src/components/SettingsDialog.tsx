// Ventana «Ajustes»: lo mismo que el menú del icono de Luzeira (brillo, girar, pantalla,
// inicio con Windows, acceso directo, importar tema, carpeta de temas, actualizaciones),
// pero a la vista.
// Cada cambio se aplica al momento; no hay botón de guardar.

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, errorMessage, type AppSettings, type DisplayOption, type EditorApi, type SettingsChange } from '../api.ts'
import { AUTO, displaySelectValue, findOption, needsConfirmation, OTHER } from '../model/settings.ts'
import { Dialog, type ToastItem } from './Dialog.tsx'
import { Field, Segmented, Toggle } from './fields.tsx'
import { IconFolder, IconUpload, IconWarn } from './icons.tsx'

/** Cada cuánto se vuelve a leer mientras está abierta (la pantalla puede conectarse). */
const REFRESH_MS = 3000
/** Tras elegir pantalla, cuándo mirar cómo ha ido (la prueba tarda un momento). */
const AFTER_CHOICE_MS = 800

interface Props {
  api: EditorApi
  /** Se ha elegido pantalla: que el editor mire pronto si hay pregunta «¿se ve bien?». */
  onDisplayChosen: () => void
  onClose: () => void
  onImported: (name: string) => void
  toast: (message: string, kind?: ToastItem['kind']) => void
}

export function SettingsDialog({ api, onDisplayChosen, onClose, onImported, toast }: Props) {
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<DisplayOption | null>(null)
  const [busy, setBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const reload = useCallback(
    () =>
      api.settings().then(
        (s) => {
          setSettings(s)
          setLoadError(null)
        },
        (e: unknown) => {
          if (e instanceof ApiError && e.status === 404) {
            setLoadError('Los ajustes solo están cuando abres el editor desde el icono de Luzeira (junto al reloj).')
          } else setLoadError(errorMessage(e))
        },
      ),
    [api],
  )

  useEffect(() => {
    void reload()
    const timer = window.setInterval(() => void reload(), REFRESH_MS)
    return () => window.clearInterval(timer)
  }, [reload])

  // `updates` va aparte (setUpdates): aquí se mezcla en plano y pisaría el objeto entero.
  const change = async (c: Omit<SettingsChange, 'updates'>) => {
    // Se ve al momento; si Luzeira no lo acepta, se vuelve a leer lo que de verdad hay.
    setSettings((s) => (s ? { ...s, ...c } : s))
    try {
      await api.updateSettings(c)
    } catch (e) {
      toast(errorMessage(e), 'error')
      void reload()
    }
  }

  const setUpdates = async (enabled: boolean) => {
    setSettings((s) => (s ? { ...s, updates: { ...s.updates, enabled } } : s))
    try {
      await api.updateSettings({ updates: enabled })
    } catch (e) {
      toast(errorMessage(e), 'error')
      void reload()
    }
  }

  const choose = async (option: DisplayOption | null, confirmed: boolean) => {
    setConfirm(null)
    try {
      await api.chooseDisplay(option?.id ?? null, confirmed)
      toast(option ? 'Probando la pantalla elegida…' : 'Vale: Luzeira buscará la pantalla ella sola.', 'info')
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
    window.setTimeout(() => {
      void reload()
      onDisplayChosen()
    }, AFTER_CHOICE_MS)
  }

  const pick = (id: string) => {
    if (!settings || id === OTHER) return
    if (id === AUTO) return void choose(null, false)
    const option = findOption(id, [settings.choices, settings.advanced])
    if (!option) return
    if (needsConfirmation(option)) setConfirm(option)
    else void choose(option, false)
  }

  const run = async (action: () => Promise<void>) => {
    setBusy(true)
    try {
      await action()
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setBusy(false)
    }
  }

  const importZip = (file: File) =>
    run(async () => {
      const name = await api.importTheme(file)
      toast('Tema importado. Lo tienes en «Mis temas».', 'ok')
      onImported(name)
    })

  const selectValue = settings ? displaySelectValue(settings) : AUTO
  return (
    <>
      <Dialog
        title="Ajustes"
        className="settings"
        onClose={onClose}
        actions={
          <button type="button" className="btn primary" onClick={onClose}>
            Cerrar
          </button>
        }
      >
        {loadError && !settings && <p className="settings-error">{loadError}</p>}
        {!loadError && !settings && <p>Cargando…</p>}
        {settings && (
          <>
            <section className="settings-section" aria-labelledby="ajustes-pantalla">
              <h3 id="ajustes-pantalla">Pantalla</h3>
              <p className="settings-status" role="status">
                {settings.display_text}
              </p>
              <Segmented
                label="Brillo"
                value={String(settings.brightness)}
                options={settings.brightness_levels.map((l) => ({ value: String(l), label: `${l} %` }))}
                onChange={(v) => void change({ brightness: Number(v) })}
              />
              <Toggle label="Girar 180°" checked={settings.flip} onChange={(v) => void change({ flip: v })} hint="Si tienes la pantalla montada boca abajo." />
              <Field label="Modelo y puerto" hint="Lo normal es dejar que Luzeira la encuentre sola. Elige aquí solo si no la encuentra o tienes varias.">
                {({ inputId, describedBy }) => (
                  <select id={inputId} className="input" aria-describedby={describedBy} value={selectValue} onChange={(e) => pick(e.target.value)}>
                    <option value={AUTO}>Detectarla sola (recomendado)</option>
                    {settings.choices.map((o, i) => (
                      <option key={o.id ?? `no-${i}`} value={o.id ?? `no-${i}`} disabled={o.id === null}>
                        {o.label}
                      </option>
                    ))}
                    {selectValue === OTHER && (
                      <option value={OTHER} disabled>
                        Elegida a mano (modo avanzado)
                      </option>
                    )}
                  </select>
                )}
              </Field>
              <details className="settings-advanced">
                <summary>Modo avanzado (cualquier puerto)</summary>
                <p className="settings-warning">
                  <IconWarn /> Solo si sabes qué hay conectado en cada puerto: si no es una pantalla, recibirá datos que no entiende.
                </p>
                <label className="sr-only" htmlFor="ajustes-avanzado">
                  Puerto y modelo en modo avanzado
                </label>
                <select id="ajustes-avanzado" className="input" value="" onChange={(e) => pick(e.target.value)}>
                  <option value="" disabled>
                    {settings.advanced.length ? 'Elige puerto y modelo…' : 'No hay ningún puerto COM'}
                  </option>
                  {settings.advanced.map((o) => (
                    <option key={o.id} value={o.id ?? ''}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </details>
            </section>

            <section className="settings-section" aria-labelledby="ajustes-inicio">
              <h3 id="ajustes-inicio">Inicio y accesos</h3>
              {settings.autostart_blocked ? (
                <>
                  <p className="settings-status">
                    Quitaste Luzeira del inicio desde Windows, así que solo se puede volver a activar allí.
                  </p>
                  <button type="button" className="btn" disabled={busy} onClick={() => void run(() => api.openStartupSettings())}>
                    Abrir Configuración de Windows
                  </button>
                </>
              ) : (
                <Toggle
                  label="Iniciar con Windows"
                  checked={settings.autostart}
                  onChange={(v) => void change({ autostart: v })}
                  hint="Luzeira se abre sola al encender el PC, sin pedir permisos de administrador."
                />
              )}
              {settings.store ? (
                <p className="settings-status">Luzeira ya está en el menú Inicio, y se actualiza desde la Store.</p>
              ) : (
                <button type="button" className="btn" disabled={busy} onClick={() => void run(async () => toast(await api.createShortcut(), 'ok'))}>
                  Crear acceso directo en el escritorio
                </button>
              )}
            </section>

            <section className="settings-section" aria-labelledby="ajustes-temas">
              <h3 id="ajustes-temas">Temas</h3>
              <div className="settings-row">
                <button type="button" className="btn" disabled={busy} onClick={() => fileRef.current?.click()}>
                  <IconUpload /> Importar tema (.zip)…
                </button>
                <button type="button" className="btn" disabled={busy} onClick={() => void run(() => api.openThemesFolder())}>
                  <IconFolder /> Abrir carpeta de temas
                </button>
              </div>
              <input
                ref={fileRef}
                type="file"
                accept=".zip,application/zip"
                hidden
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  e.target.value = '' // que se pueda elegir el mismo otra vez
                  if (file) void importZip(file)
                }}
              />
            </section>

            {settings.updates.can_check && (
              <section className="settings-section" aria-labelledby="ajustes-actualizaciones">
                <h3 id="ajustes-actualizaciones">Actualizaciones</h3>
                <p className="settings-status" role="status">
                  {settings.updates.available
                    ? `Tienes la ${settings.updates.version}. Hay una versión nueva: la ${settings.updates.available}.`
                    : `Tienes la versión ${settings.updates.version}.`}
                </p>
                <Toggle
                  label="Avisarme de versiones nuevas"
                  checked={settings.updates.enabled === true}
                  onChange={(v) => void setUpdates(v)}
                  hint="Una vez al día mira en GitHub si hay versión nueva. No envía nada tuyo y nunca instala sin que pulses «Actualizar»."
                />
                <div className="settings-row">
                  {settings.updates.available && (
                    <button
                      type="button"
                      className="btn primary"
                      disabled={busy || settings.updates.busy}
                      onClick={() => void run(async () => toast(await api.installUpdate(), 'ok'))}
                    >
                      Actualizar a la {settings.updates.available}
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn"
                    disabled={busy || settings.updates.busy}
                    onClick={() =>
                      void run(async () => {
                        toast(await api.checkUpdates(), 'info')
                        // Lo comprobado lo guarda el hilo del icono un instante después.
                        window.setTimeout(() => void reload(), AFTER_CHOICE_MS)
                      })
                    }
                  >
                    Buscar ahora
                  </button>
                </div>
              </section>
            )}
          </>
        )}
      </Dialog>

      {confirm && (
        <Dialog
          title={confirm.id?.startsWith('avanzado:') ? 'Modo avanzado' : 'Probar un modelo en beta'}
          onClose={() => setConfirm(null)}
          actions={
            <>
              <button type="button" className="btn primary" onClick={() => setConfirm(null)}>
                No
              </button>
              <button type="button" className="btn danger" onClick={() => void choose(confirm, true)}>
                Sí, probar
              </button>
            </>
          }
        >
          <p className="settings-confirm">{confirm.warning}</p>
        </Dialog>
      )}
    </>
  )
}
