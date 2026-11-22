// Ventana «Ajustes»: lo mismo que el menú del icono de Luzeira (brillo, girar, pantalla,
// inicio con Windows, acceso directo, importar tema, carpeta de temas, actualizaciones),
// pero a la vista.
// Cada cambio se aplica al momento; no hay botón de guardar.

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, errorMessage, type AppSettings, type DisplayOption, type EditorApi, type SettingsChange } from '../api.ts'
import { AUTO, displaySelectValue, findOption, needsConfirmation, OTHER } from '../model/settings.ts'
import { useT } from '../i18n/contexto.ts'
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
  /** Se ha cambiado el idioma: que el editor lo pregunte ya a Luzeira y se traduzca. */
  onLanguageChanged: () => void
  onClose: () => void
  onImported: (name: string) => void
  toast: (message: string, kind?: ToastItem['kind']) => void
}

export function SettingsDialog({ api, onDisplayChosen, onLanguageChanged, onClose, onImported, toast }: Props) {
  const t = useT()
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
            setLoadError(t('ajustes.sin_bandeja'))
          } else setLoadError(errorMessage(e))
        },
      ),
    [api, t],
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
      if (c.language !== undefined) onLanguageChanged()
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
      toast(option ? t('ajustes.probando') : t('ajustes.auto.elegida'), 'info')
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
      toast(t('ajustes.importado'), 'ok')
      onImported(name)
    })

  const selectValue = settings ? displaySelectValue(settings) : AUTO
  return (
    <>
      <Dialog
        title={t('ajustes.titulo')}
        className="settings"
        onClose={onClose}
        actions={
          <button type="button" className="btn primary" onClick={onClose}>
            {t('comun.cerrar')}
          </button>
        }
      >
        {loadError && !settings && <p className="settings-error">{loadError}</p>}
        {!loadError && !settings && <p>{t('comun.cargando')}</p>}
        {settings && (
          <>
            <section className="settings-section" aria-labelledby="ajustes-pantalla">
              <h3 id="ajustes-pantalla">{t('ajustes.pantalla')}</h3>
              <p className="settings-status" role="status">
                {settings.display_text}
              </p>
              <Segmented
                label={t('ajustes.brillo')}
                value={String(settings.brightness)}
                options={settings.brightness_levels.map((l) => ({ value: String(l), label: t('ajustes.brillo.nivel', { n: l }) }))}
                onChange={(v) => void change({ brightness: Number(v) })}
              />
              <Toggle label={t('ajustes.girar')} checked={settings.flip} onChange={(v) => void change({ flip: v })} hint={t('ajustes.girar.ayuda')} />
              <Field label={t('ajustes.modelo')} hint={t('ajustes.modelo.ayuda')}>
                {({ inputId, describedBy }) => (
                  <select id={inputId} className="input" aria-describedby={describedBy} value={selectValue} onChange={(e) => pick(e.target.value)}>
                    <option value={AUTO}>{t('ajustes.modelo.auto')}</option>
                    {settings.choices.map((o, i) => (
                      <option key={o.id ?? `no-${i}`} value={o.id ?? `no-${i}`} disabled={o.id === null}>
                        {o.label}
                      </option>
                    ))}
                    {selectValue === OTHER && (
                      <option value={OTHER} disabled>
                        {t('ajustes.modelo.otra')}
                      </option>
                    )}
                  </select>
                )}
              </Field>
              <details className="settings-advanced">
                <summary>{t('ajustes.avanzado.resumen')}</summary>
                <p className="settings-warning">
                  <IconWarn /> {t('ajustes.avanzado.aviso')}
                </p>
                <label className="sr-only" htmlFor="ajustes-avanzado">
                  {t('ajustes.avanzado.etiqueta')}
                </label>
                <select id="ajustes-avanzado" className="input" value="" onChange={(e) => pick(e.target.value)}>
                  <option value="" disabled>
                    {settings.advanced.length ? t('ajustes.avanzado.elige') : t('ajustes.avanzado.ninguno')}
                  </option>
                  {settings.advanced.map((o) => (
                    <option key={o.id} value={o.id ?? ''}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </details>
            </section>

            <section className="settings-section" aria-labelledby="ajustes-idioma">
              <h3 id="ajustes-idioma">{t('ajustes.idioma.titulo')}</h3>
              <select
                className="input"
                aria-labelledby="ajustes-idioma"
                aria-describedby="ajustes-idioma-ayuda"
                value={settings.language}
                onChange={(e) => void change({ language: e.target.value })}
              >
                <option value="auto">{t('ajustes.idioma.automatico')}</option>
                {settings.languages.map((l) => (
                  <option key={l.code} value={l.code} lang={l.code}>
                    {l.name}
                  </option>
                ))}
              </select>
              <p className="hint" id="ajustes-idioma-ayuda">
                {t('ajustes.idioma.ayuda')}
              </p>
            </section>

            <section className="settings-section" aria-labelledby="ajustes-inicio">
              <h3 id="ajustes-inicio">{t('ajustes.inicio')}</h3>
              {settings.autostart_blocked ? (
                <>
                  <p className="settings-status">
                    {t('ajustes.inicio.bloqueado')}
                  </p>
                  <button type="button" className="btn" disabled={busy} onClick={() => void run(() => api.openStartupSettings())}>
                    {t('ajustes.inicio.configuracion')}
                  </button>
                </>
              ) : (
                <Toggle
                  label={t('ajustes.inicio.windows')}
                  checked={settings.autostart}
                  onChange={(v) => void change({ autostart: v })}
                  hint={t('ajustes.inicio.windows.ayuda')}
                />
              )}
              {settings.store ? (
                <p className="settings-status">{t('ajustes.inicio.store')}</p>
              ) : (
                <button type="button" className="btn" disabled={busy} onClick={() => void run(async () => toast(await api.createShortcut(), 'ok'))}>
                  {t('ajustes.inicio.acceso')}
                </button>
              )}
            </section>

            <section className="settings-section" aria-labelledby="ajustes-temas">
              <h3 id="ajustes-temas">{t('ajustes.temas')}</h3>
              <div className="settings-row">
                <button type="button" className="btn" disabled={busy} onClick={() => fileRef.current?.click()}>
                  <IconUpload /> {t('ajustes.temas.importar')}
                </button>
                <button type="button" className="btn" disabled={busy} onClick={() => void run(() => api.openThemesFolder())}>
                  <IconFolder /> {t('ajustes.temas.carpeta')}
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
                <h3 id="ajustes-actualizaciones">{t('ajustes.actualizaciones')}</h3>
                <p className="settings-status" role="status">
                  {settings.updates.available
                    ? t('ajustes.actualizaciones.hay', { version: settings.updates.version, nueva: settings.updates.available })
                    : t('ajustes.actualizaciones.tienes', { version: settings.updates.version })}
                </p>
                <Toggle
                  label={t('ajustes.actualizaciones.avisar')}
                  checked={settings.updates.enabled === true}
                  onChange={(v) => void setUpdates(v)}
                  hint={t('ajustes.actualizaciones.avisar.ayuda')}
                />
                <div className="settings-row">
                  {settings.updates.available && (
                    <button
                      type="button"
                      className="btn primary"
                      disabled={busy || settings.updates.busy}
                      onClick={() => void run(async () => toast(await api.installUpdate(), 'ok'))}
                    >
                      {t('comun.actualizar_a', { version: settings.updates.available })}
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
                    {t('ajustes.actualizaciones.buscar')}
                  </button>
                </div>
              </section>
            )}
          </>
        )}
      </Dialog>

      {confirm && (
        <Dialog
          title={confirm.id?.startsWith('avanzado:') ? t('ajustes.confirmar.avanzado') : t('ajustes.confirmar.beta')}
          onClose={() => setConfirm(null)}
          actions={
            <>
              <button type="button" className="btn primary" onClick={() => setConfirm(null)}>
                {t('comun.no')}
              </button>
              <button type="button" className="btn danger" onClick={() => void choose(confirm, true)}>
                {t('ajustes.confirmar.si')}
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
