// El editor: une el estado central (reducer) con el servidor. Aquí viven los efectos
// con tiempo: guardado automático (800 ms), vista previa (300 ms, cancelando la
// anterior), lectura de sensores y atajos de teclado.

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { ApiError, errorMessage, type EditorApi, type Info, type SensorInfo, type ThemeSummary } from './api.ts'
import { initialState, reducer, selectedWidget, indexOfId } from './model/editorState.ts'
import { APPLY_BLOCK_TEXT, applyBlock, appliedMessage, deletedMessage, DEFAULT_THEME, type ApplyBlock, flushOutcome, isDroppableImage, type FlushResult, type SaveOutcome } from './model/contract.ts'
import { formatClock } from './model/format.ts'
import { baseName, createWidget, normalizeTheme, CLOCK_FORMAT_INFO } from './model/theme.ts'
import { LIMITS, MAX_WIDGETS, type Theme, type Widget, type WidgetType } from './model/types.ts'
import { validateTheme, widgetIndexOf, type Issue } from './model/validate.ts'
import { CanvasArea, type ZoomMode } from './components/CanvasArea.tsx'
import { Dialog, Toasts, type ToastItem } from './components/Dialog.tsx'
import { SettingsDialog } from './components/SettingsDialog.tsx'
import { LayersPanel } from './components/LayersPanel.tsx'
import { PropertiesPanel } from './components/PropertiesPanel.tsx'
import { ThemePanel } from './components/ThemePanel.tsx'
import { TopBar, type SaveStatus } from './components/TopBar.tsx'
import { Tooltips } from './components/Tooltips.tsx'
import { IconGrid, IconWarn } from './components/icons.tsx'
import type { ResourceKind, Services } from './components/services.ts'

const SAVE_DELAY_MS = 800
const PREVIEW_DELAY_MS = 300
const PREVIEW_REFRESH_MS = 5000 // para que el reloj y los sensores de la vista previa se muevan
const SENSORS_REFRESH_MS = 2000

interface Current {
  name: string
  builtin: boolean
  files: string[]
}

type DialogState =
  | { kind: 'saveAs'; reason?: string; thenApply?: boolean }
  | { kind: 'restore' }
  | { kind: 'applyFirst'; why: ApplyBlock }
  | { kind: 'removeWidget'; id: string }
  | { kind: 'deleteTheme'; onScreen: boolean }
  | { kind: 'discard'; next: string }
  | { kind: 'settings' }
  | null

/** Cada cuánto se pregunta el estado de la pantalla (ms). */
const INFO_REFRESH_MS = 3000

interface Props {
  api: EditorApi
  info: Info | null
  initialThemes: ThemeSummary[]
  initialName: string
}

declare global {
  interface Window {
    /** Para la ventana de Luzeira: guarda lo pendiente antes de cerrar. */
    luzeiraEditor?: { flush: () => Promise<FlushResult> }
  }
}

export function Editor({ api, info: initialInfo, initialThemes, initialName }: Props) {
  // Estado de la pantalla (arriba a la derecha). Se vuelve a preguntar cada pocos segundos:
  // leído solo al abrir, se quedaba para siempre en «Preparando la imagen…».
  const [info, setInfo] = useState<Info | null>(initialInfo)
  const aliveRef = useRef(true)
  const refreshInfo = useCallback(
    () =>
      void api
        .info()
        .then((next) => aliveRef.current && setInfo(next))
        .catch(() => undefined), // sin respuesta, se deja lo último conocido
    [api],
  )
  useEffect(() => {
    aliveRef.current = true
    const timer = window.setInterval(refreshInfo, INFO_REFRESH_MS)
    return () => {
      aliveRef.current = false
      window.clearInterval(timer)
    }
  }, [refreshInfo])
  const [answering, setAnswering] = useState(false)
  const [state, dispatch] = useReducer(reducer, undefined, () => initialState())
  const [themes, setThemes] = useState(initialThemes)
  const [current, setCurrent] = useState<Current | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [status, setStatus] = useState<SaveStatus>({ kind: 'saved' })
  const [serverIssues, setServerIssues] = useState<Issue[]>([])
  const [previewIssues, setPreviewIssues] = useState<Issue[]>([])
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [sensors, setSensors] = useState<SensorInfo[]>([])
  const [zoom, setZoom] = useState<ZoomMode>('fit')
  const [snap, setSnap] = useState(true)
  const [outlines, setOutlines] = useState(true)
  const [pointer, setPointer] = useState<{ x: number; y: number } | null>(null)
  const [dialog, setDialog] = useState<DialogState>(null)
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const [applying, setApplying] = useState(false)
  const [tick, setTick] = useState(0)

  const theme = state.present.theme
  const ids = state.present.ids

  // --- Avisos ---------------------------------------------------------------------
  const toastId = useRef(0)
  const toast = useCallback((message: string, kind: ToastItem['kind'] = 'info', action?: ToastItem['action']) => {
    const id = ++toastId.current
    setToasts((t) => [...t.slice(-2), { id, message, kind, action }])
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), action ? 7000 : 4500)
  }, [])

  // --- Validación -----------------------------------------------------------------
  const clientIssues = useMemo(() => (current ? validateTheme(theme) : []), [theme, current])
  // Mientras el cliente ve errores, mandan esos; si no, los últimos del servidor.
  const issues = clientIssues.length ? clientIssues : dedupe([...serverIssues, ...previewIssues])
  const errorIds = useMemo(() => {
    const set = new Set<string>()
    for (const i of issues) {
      const index = widgetIndexOf(i.path)
      if (index !== null && ids[index]) set.add(ids[index])
    }
    return set
  }, [issues, ids])

  // --- Carga de un tema -------------------------------------------------------------
  const savedRef = useRef<Theme | null>(null) // último tema guardado (por referencia)
  const openedRef = useRef<Theme | null>(null) // cómo estaba al abrirlo (para «Restaurar»)
  const loadTheme = useCallback(
    async (name: string) => {
      try {
        const doc = await api.getTheme(name)
        const t = normalizeTheme(doc.theme)
        savedRef.current = t
        openedRef.current = t
        setCurrent({ name: doc.name, builtin: doc.builtin, files: doc.files })
        dispatch({ type: 'load', theme: t })
        setServerIssues([])
        setPreviewIssues([])
        setPreviewError(null)
        setStatus({ kind: doc.builtin ? 'readonly' : 'saved' })
        setLoadError(null)
      } catch (e) {
        setLoadError(errorMessage(e))
      }
    },
    [api],
  )

  useEffect(() => {
    void loadTheme(initialName)
  }, [loadTheme, initialName])

  // --- Guardado automático ------------------------------------------------------------
  const latest = useRef({ theme, current })
  latest.current = { theme, current }
  const saveTimer = useRef<number | null>(null)
  const saveChain = useRef<Promise<unknown>>(Promise.resolve())
  /** Cómo acabó el último guardado (para decirle a la ventana si queda algo). */
  const lastSave = useRef<SaveOutcome | undefined>(undefined)

  const enqueueSave = useCallback(
    (name: string, t: Theme) => {
      const next = saveChain.current.then(async (): Promise<void> => {
        if (savedRef.current === t || latest.current.current?.name !== name) return
        setStatus({ kind: 'saving' })
        try {
          await api.saveTheme(name, t)
          savedRef.current = t
          lastSave.current = 'ok'
          setServerIssues([])
          setThemes((list) => list.map((x) => (x.name === name ? { ...x, title: t.name } : x)))
          if (latest.current.theme === t) setStatus({ kind: 'saved' })
        } catch (e) {
          const withDetails = e instanceof ApiError && e.details.length > 0
          lastSave.current = withDetails ? 'invalid' : 'error'
          if (withDetails) setServerIssues(e.details)
          setStatus({ kind: 'error', message: errorMessage(e) })
        }
      })
      saveChain.current = next
      return next
    },
    [api],
  )

  useEffect(() => {
    if (!current) return
    const dirty = theme !== savedRef.current
    if (current.builtin) {
      setStatus({ kind: dirty ? 'readonly-dirty' : 'readonly' })
      return
    }
    if (!dirty) return
    if (clientIssues.length) {
      setStatus({ kind: 'invalid' })
      return
    }
    setStatus({ kind: 'pending' })
    const name = current.name
    saveTimer.current = window.setTimeout(() => {
      saveTimer.current = null
      void enqueueSave(name, theme)
    }, SAVE_DELAY_MS)
    return () => {
      if (saveTimer.current !== null) window.clearTimeout(saveTimer.current)
      saveTimer.current = null
    }
  }, [theme, current, clientIssues, enqueueSave])

  /**
   * Guarda ya lo pendiente (antes de cambiar de tema, aplicar o cerrar) y dice si queda
   * algo que no se ha podido guardar. Es lo que llama la ventana al cerrar
   * (`window.luzeiraEditor.flush()`, ver «Remate 2» en docs/planning/fase-5-editor.md).
   */
  const flush = useCallback(async (): Promise<FlushResult> => {
    const { theme: t, current: c } = latest.current
    if (saveTimer.current !== null) {
      window.clearTimeout(saveTimer.current)
      saveTimer.current = null
    }
    if (!c) return { saved: true, pending: 'none' }
    const invalid = validateTheme(t).length > 0
    if (!c.builtin && t !== savedRef.current && !invalid) await enqueueSave(c.name, t)
    else await saveChain.current
    return flushOutcome({ dirty: t !== savedRef.current, builtin: c.builtin, invalid, lastSave: lastSave.current })
  }, [enqueueSave])

  useEffect(() => {
    window.luzeiraEditor = { flush }
    // Al descargar la página no da tiempo a esperar la cola: se manda el PUT ya (con
    // `keepalive`, que lo deja terminar aunque la página se vaya).
    const onUnload = () => {
      const { theme: t, current: c } = latest.current
      if (saveTimer.current !== null) window.clearTimeout(saveTimer.current)
      saveTimer.current = null
      if (c && !c.builtin && t !== savedRef.current && validateTheme(t).length === 0) {
        void api.saveTheme(c.name, t).catch(() => {})
      }
    }
    window.addEventListener('beforeunload', onUnload)
    return () => {
      window.removeEventListener('beforeunload', onUnload)
      if (window.luzeiraEditor?.flush === flush) delete window.luzeiraEditor
    }
  }, [flush, api])

  // Soltar un fichero fuera del lienzo no debe hacer que la ventana lo abra (y salga
  // del editor). El lienzo y la lista de capas tratan antes lo suyo.
  useEffect(() => {
    const stop = (e: DragEvent) => {
      const handled = e.defaultPrevented // ya lo aceptó el lienzo o la lista de capas
      e.preventDefault()
      if (e.type === 'dragover' && e.dataTransfer && !handled) e.dataTransfer.dropEffect = 'none'
    }
    window.addEventListener('dragover', stop)
    window.addEventListener('drop', stop)
    return () => {
      window.removeEventListener('dragover', stop)
      window.removeEventListener('drop', stop)
    }
  }, [])

  // --- Vista previa -------------------------------------------------------------------
  const previewAbort = useRef<AbortController | null>(null)
  const previewUrlRef = useRef<string | null>(null)
  useEffect(() => {
    if (!current || clientIssues.length) return // se mantiene la última buena
    const name = current.name
    const timer = window.setTimeout(async () => {
      previewAbort.current?.abort()
      const controller = new AbortController()
      previewAbort.current = controller
      try {
        const blob = await api.preview(name, theme, controller.signal)
        // null = el servidor la dio por superada (ya pinta otra más nueva): nada que hacer.
        if (controller.signal.aborted || blob === null) return
        const url = URL.createObjectURL(blob)
        if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current)
        previewUrlRef.current = url
        setPreviewUrl(url)
        setPreviewIssues([])
        setPreviewError(null)
      } catch (e) {
        if (controller.signal.aborted || (e instanceof DOMException && e.name === 'AbortError')) return
        if (e instanceof ApiError && e.details.length) setPreviewIssues(e.details)
        setPreviewError(errorMessage(e))
      }
    }, PREVIEW_DELAY_MS)
    return () => window.clearTimeout(timer)
  }, [api, theme, current, clientIssues, tick])

  useEffect(() => {
    const t = window.setInterval(() => {
      if (document.visibilityState === 'visible') setTick((n) => n + 1)
    }, PREVIEW_REFRESH_MS)
    return () => {
      window.clearInterval(t)
      previewAbort.current?.abort()
    }
  }, [])

  // --- Sensores en vivo ---------------------------------------------------------------
  useEffect(() => {
    let alive = true
    const read = () => {
      if (document.visibilityState !== 'visible') return
      api.sensors().then(
        (list) => alive && setSensors(list),
        () => {},
      )
    }
    read()
    const t = window.setInterval(read, SENSORS_REFRESH_MS)
    return () => {
      alive = false
      window.clearInterval(t)
    }
  }, [api])

  // --- Subidas ------------------------------------------------------------------------
  /** En un tema incluido no se puede subir nada: se pide antes «Guardar como». */
  const askSaveAsForUpload = useCallback(() => {
    setDialog({ kind: 'saveAs', reason: 'Para añadir tus imágenes o letras, primero guarda una copia del tema con tu nombre.' })
  }, [])

  const uploadFile = useCallback(
    async (file: File): Promise<{ path: string; file: File } | null> => {
      const c = latest.current.current
      if (!c) return null
      if (c.builtin) {
        askSaveAsForUpload()
        return null
      }
      if (file.size > 10 * 1024 * 1024) {
        toast('El fichero pasa de 10 MB. Prueba con uno más pequeño.', 'error')
        return null
      }
      try {
        const path = await api.uploadResource(c.name, file)
        setCurrent((cur) => (cur && cur.name === c.name && !cur.files.includes(path) ? { ...cur, files: [...cur.files, path] } : cur))
        toast(`Subido: ${baseName(path)}`, 'ok')
        return { path, file }
      } catch (e) {
        toast(errorMessage(e), 'error')
        return null
      }
    },
    [api, toast, askSaveAsForUpload],
  )

  const upload = useCallback(
    async (kind: ResourceKind): Promise<{ path: string; file: File } | null> => {
      const c = latest.current.current
      if (!c) return null
      if (c.builtin) {
        askSaveAsForUpload()
        return null
      }
      const file = await pickFile(kind === 'image' ? '.png,.jpg,.jpeg,.gif,image/png,image/jpeg,image/gif' : '.ttf,.otf')
      return file ? uploadFile(file) : null
    },
    [uploadFile, askSaveAsForUpload],
  )

  /** Imagen soltada sobre el lienzo: se sube y se crea un elemento Imagen en ese punto. */
  const dropImage = useCallback(
    async (file: File, point: { x: number; y: number }) => {
      if (!isDroppableImage(file.name)) {
        toast('Aquí solo se pueden soltar imágenes PNG, JPG o GIF. Las letras se eligen en el panel de la derecha.', 'error')
        return
      }
      if (latest.current.theme.widgets.length >= MAX_WIDGETS) {
        toast(`Un tema admite como mucho ${MAX_WIDGETS} elementos.`, 'error')
        return
      }
      const r = await uploadFile(file)
      if (!r) return
      const size = await imageSize(r.file)
      dispatch({
        type: 'add',
        widget: createWidget('image', latest.current.theme, { file: r.path, imageSize: size ?? undefined, center: point }),
      })
    },
    [uploadFile, toast],
  )

  // --- Acciones -----------------------------------------------------------------------
  const addWidget = useCallback(
    async (type: WidgetType) => {
      if (theme.widgets.length >= MAX_WIDGETS) return
      const firstNumber = sensors.find((s) => s.kind === 'number')?.id
      if (type === 'image') {
        const r = await upload('image')
        if (!r) return
        const size = await imageSize(r.file)
        dispatch({ type: 'add', widget: createWidget('image', latest.current.theme, { file: r.path, imageSize: size ?? undefined }) })
        return
      }
      dispatch({ type: 'add', widget: createWidget(type, theme, { sensor: firstNumber }) })
    },
    [theme, sensors, upload],
  )

  const stateRef = useRef(state)
  stateRef.current = state
  const removeWidget = useCallback(
    (id: string) => {
      const doc = stateRef.current.present
      const index = indexOfId(doc, id)
      if (index < 0) return
      const widget = doc.theme.widgets[index]
      dispatch({ type: 'remove', id })
      // «Deshacer» devuelve justo este elemento (no deshace el último paso, que podría
      // ser otro cambio hecho después).
      toast('Elemento borrado.', 'info', { label: 'Deshacer', run: () => dispatch({ type: 'restore', id, widget, index }) })
    },
    [toast],
  )

  // «¿Se ve bien?» de una pantalla elegida en «Ajustes». Cerrarla cuenta como «No», igual
  // que el diálogo de Windows: sin respuesta clara, no se recuerda una pantalla dudosa.
  const answerQuestion = async (looksRight: boolean) => {
    if (answering) return
    setAnswering(true)
    try {
      toast(await api.answerQuestion(looksRight), looksRight ? 'ok' : 'info')
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setInfo((i) => (i ? { ...i, question: null } : i))
      setAnswering(false)
      refreshInfo()
    }
  }

  const selectTheme = async (name: string) => {
    if (!current || name === current.name) return
    if (current.builtin && theme !== savedRef.current) {
      setDialog({ kind: 'discard', next: name })
      return
    }
    await flush()
    await loadTheme(name)
  }

  const saveAs = async (title: string, thenApply = false) => {
    if (!current) return
    // La copia lleva ya lo editado con el nombre nuevo (un solo paso en el servidor).
    const edited: Theme = { ...latest.current.theme, name: title }
    const issues = validateTheme(edited)
    if (issues.length) {
      setDialog(null)
      toast('Corrige primero los campos marcados en rojo y vuelve a «Guardar como…».', 'error')
      return
    }
    try {
      await flush()
      const newName = await api.copyTheme(current.name, title, edited)
      setThemes(await api.listThemes())
      // Se conserva el historial: el tema pasa a ser el nuevo y ya está guardado tal cual
      // (mismo objeto), así que el guardado automático no tiene nada que hacer.
      savedRef.current = edited
      openedRef.current = edited
      setServerIssues([])
      setStatus({ kind: 'saved' })
      setCurrent({ name: newName, builtin: false, files: current.files })
      dispatch({ type: 'replaceTheme', theme: edited })
      setDialog(null)
      toast(`Guardado en «Mis temas» como «${title}».`, 'ok')
      if (thenApply) await applyNow(newName, edited)
    } catch (e) {
      if (e instanceof ApiError && e.details.length) {
        setServerIssues(e.details)
        setDialog(null)
      }
      toast(errorMessage(e), 'error')
    }
  }

  /** «Aplicar»: si antes hay que guardar algo, pregunta primero (applyBlock). */
  const requestApply = () => {
    if (!current) return
    const why = applyBlock({
      builtin: current.builtin,
      dirty: theme !== savedRef.current,
      invalid: clientIssues.length > 0,
    })
    if (why) setDialog({ kind: 'applyFirst', why })
    else void applyNow(current.name, theme)
  }

  /** Guarda lo pendiente (si se puede) y pone el tema en la pantalla. */
  const applyNow = async (name: string, shown: Theme) => {
    setDialog(null)
    setApplying(true)
    try {
      await flush()
      await api.apply(name)
      // El aviso dice lo que pasa de verdad en la pantalla (puede no estar conectada).
      const now = await api.info().catch(() => null)
      if (now) setInfo(now)
      const m = appliedMessage(shown.name, now)
      toast(m.message, m.kind)
    } catch (e) {
      toast(errorMessage(e), 'error')
    } finally {
      setApplying(false)
    }
  }

  /** «Guardar»: guarda ya, sin esperar al guardado automático. */
  const saveNow = async () => {
    const result = await flush()
    if (result.saved) toast('Guardado.', 'ok')
    else toast('No se ha podido guardar: corrige los campos marcados en rojo.', 'error')
  }

  /** «Restaurar»: el tema vuelve a como estaba al abrirlo (se puede deshacer). */
  const restore = () => {
    setDialog(null)
    if (openedRef.current) dispatch({ type: 'replaceTheme', theme: openedRef.current })
    toast('Restaurado como estaba al abrirlo. Puedes deshacerlo con Ctrl+Z.', 'ok')
  }

  const deleteTheme = async (onScreen: boolean) => {
    if (!current || current.builtin) return
    try {
      if (saveTimer.current !== null) window.clearTimeout(saveTimer.current)
      saveTimer.current = null
      await saveChain.current
      // El tema en pantalla no se puede borrar (el servidor da 409): antes se pone el de
      // siempre, como haría el usuario a mano.
      if (onScreen) await api.apply(DEFAULT_THEME)
      await api.deleteTheme(current.name)
      const list = await api.listThemes()
      setThemes(list)
      setDialog(null)
      toast(deletedMessage(onScreen), 'ok')
      const next = list.find((t) => t.name === DEFAULT_THEME) ?? list[0]
      if (next) await loadTheme(next.name)
    } catch (e) {
      toast(errorMessage(e), 'error')
    }
  }

  // --- Atajos de teclado ------------------------------------------------------------
  const keyState = useRef({ selectedId: state.selectedId, dialog, addWidget })
  keyState.current = { selectedId: state.selectedId, dialog, addWidget }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const { selectedId, dialog: d } = keyState.current
      if (d) return
      const target = e.target as HTMLElement
      const editing = target.closest('input, textarea, select, [contenteditable=true]') !== null
      const ctrl = e.ctrlKey || e.metaKey
      const key = e.key.toLowerCase()
      // En un campo de texto, Ctrl+Z es el deshacer del propio campo.
      if (editing) return
      if (ctrl && key === 'z' && !e.shiftKey) dispatch({ type: 'undo' })
      else if (ctrl && (key === 'y' || (key === 'z' && e.shiftKey))) dispatch({ type: 'redo' })
      else if (ctrl && key === 'd' && selectedId) dispatch({ type: 'duplicate', id: selectedId })
      else if ((e.key === 'Delete' || e.key === 'Backspace') && selectedId) setDialog({ kind: 'removeWidget', id: selectedId })
      else if (e.key === 'Escape' && selectedId) dispatch({ type: 'select', id: null })
      else if (e.key.startsWith('Arrow') && selectedId && !e.altKey && !target.closest('[role=radiogroup]')) {
        const step = e.shiftKey ? 10 : 1
        const dx = e.key === 'ArrowLeft' ? -step : e.key === 'ArrowRight' ? step : 0
        const dy = e.key === 'ArrowUp' ? -step : e.key === 'ArrowDown' ? step : 0
        dispatch({ type: 'nudge', id: selectedId, dx, dy, at: Date.now() })
      } else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // --- Render -------------------------------------------------------------------------
  const labels = useMemo(() => theme.widgets.map((w) => widgetLabel(w, sensors)), [theme.widgets, sensors])

  if (loadError && !current) {
    return (
      <div className="fullscreen-msg">
        <div className="card">
          <IconWarn />
          <h1>No se ha podido abrir el tema</h1>
          <p>{loadError}</p>
          <button type="button" className="btn primary" onClick={() => void loadTheme(initialName)}>
            Reintentar
          </button>
        </div>
      </div>
    )
  }
  if (!current) {
    return (
      <div className="fullscreen-msg">
        <div className="spinner" aria-label="Cargando" />
      </div>
    )
  }

  const services: Services = { api, themeName: current.name, builtin: current.builtin, files: current.files, sensors, upload }
  const sel = selectedWidget(state)
  const selIndex = indexOfId(state.present, state.selectedId)
  const selIssues = issues.filter((i) => widgetIndexOf(i.path) === selIndex)

  return (
    <div className="app">
      <TopBar
        info={info}
        themes={themes}
        current={current.name}
        builtin={current.builtin}
        canUndo={state.past.length > 0}
        canRedo={state.future.length > 0}
        status={status}
        applying={applying}
        onSelectTheme={(n) => void selectTheme(n)}
        onUndo={() => dispatch({ type: 'undo' })}
        onRedo={() => dispatch({ type: 'redo' })}
        onSaveAs={() => setDialog({ kind: 'saveAs' })}
        onApply={requestApply}
        onSave={() => void saveNow()}
        onRestore={() => setDialog({ kind: 'restore' })}
        canRestore={theme !== openedRef.current}
        onDeleteTheme={() =>
          void api
            .info()
            .catch(() => null)
            .then((info) => setDialog({ kind: 'deleteTheme', onScreen: info?.active_theme === current?.name }))
        }
        onSettings={() => setDialog({ kind: 'settings' })}
      />
      <div className="banner-area">
        {current.builtin && (
          <div className="banner" role="note">
            <IconWarn />
            <p>
              <strong>Los temas incluidos no se pueden cambiar.</strong> Puedes probar cambios aquí, pero para guardarlos crea tu copia con «Guardar como…».
            </p>
            <button type="button" className="btn primary small" onClick={() => setDialog({ kind: 'saveAs' })}>
              Guardar como…
            </button>
          </div>
        )}
        {previewError && clientIssues.length === 0 && (
          <div className="banner error" role="alert">
            <IconWarn />
            <p>
              <strong>La vista previa no se ha actualizado:</strong> {previewError} Se muestra la última que salió bien.
            </p>
          </div>
        )}
      </div>

      <LayersPanel
        theme={theme}
        ids={ids}
        labels={labels}
        selectedId={state.selectedId}
        errorIds={errorIds}
        dispatch={dispatch}
        onAdd={(t) => void addWidget(t)}
      />

      <main className="center" aria-label="Lienzo">
        <CanvasArea
          theme={theme}
          ids={ids}
          selectedId={state.selectedId}
          zoomMode={zoom}
          snap={snap}
          showOutlines={outlines}
          previewUrl={previewUrl}
          previewStale={clientIssues.length > 0}
          simulated={api.simulated}
          errorIds={errorIds}
          labels={labels}
          dispatch={dispatch}
          onPointerPos={setPointer}
          onDropFile={(file, point) => void dropImage(file, point)}
        />
        <div className="canvas-bar">
          <span className="coords" aria-live="off" title="Posición del ratón en píxeles de la pantalla">
            {pointer ? `x ${pointer.x} · y ${pointer.y}` : 'x — · y —'}
          </span>
          <label className="toggle" title="Al mover y redimensionar, los bordes caen en múltiplos de 4 píxeles. Mantén Alt para soltarlo un momento.">
            <input type="checkbox" role="switch" checked={snap} onChange={(e) => setSnap(e.target.checked)} />
            <IconGrid /> <span className="long">Rejilla e imán (4 px)</span><span className="short">Imán</span>
          </label>
          <label className="toggle" title="Dibuja el contorno de todos los elementos para encontrarlos aunque estén vacíos.">
            <input type="checkbox" role="switch" checked={outlines} onChange={(e) => setOutlines(e.target.checked)} />
            Contornos
          </label>
          <label title="Tamaño del lienzo en tu monitor (no cambia la pantalla real)">
            <span className="zoom-word">Zoom </span>
            <select className="input" aria-label="Zoom" style={{ width: 92, height: 26, display: 'inline-block' }} value={String(zoom)} onChange={(e) => setZoom(e.target.value === 'fit' ? 'fit' : (Number(e.target.value) as ZoomMode))}>
              <option value="fit">Ajustar</option>
              <option value={1}>×1</option>
              <option value={2}>×2</option>
              <option value={3}>×3</option>
            </select>
          </label>
          <span className="spacer" />
          <span className="help-keys">
            <kbd>←↑→↓</kbd> mover (<kbd>Mayús</kbd> 10 px) · <kbd>Supr</kbd> borrar · <kbd>Ctrl</kbd>+<kbd>D</kbd> duplicar · <kbd>Ctrl</kbd>+<kbd>Z</kbd>/<kbd>Y</kbd> · <kbd>Alt</kbd>+clic capa de debajo
          </span>
          <span
            className="btn small icon ghost keys-button"
            role="note"
            tabIndex={0}
            aria-label="Atajos de teclado: flechas para mover (Mayús, 10 píxeles), Supr para borrar, Ctrl+D para duplicar, Ctrl+Z y Ctrl+Y para deshacer y rehacer, Alt+clic para seleccionar la capa de debajo"
            title={'Atajos de teclado\n←↑→↓  mover (Mayús: 10 px)\nSupr  borrar\nCtrl+D  duplicar\nCtrl+Z / Ctrl+Y  deshacer / rehacer\nAlt+clic  seleccionar la capa de debajo'}
          >
            ?
          </span>
        </div>
      </main>

      <aside className="right" aria-label="Propiedades">
        {sel && state.selectedId ? (
          <PropertiesPanel
            key={state.selectedId}
            theme={theme}
            widget={sel}
            id={state.selectedId}
            issues={selIssues}
            services={services}
            dispatch={dispatch}
            onDuplicate={() => state.selectedId && dispatch({ type: 'duplicate', id: state.selectedId })}
            onRemove={() => state.selectedId && setDialog({ kind: 'removeWidget', id: state.selectedId })}
          />
        ) : (
          <ThemePanel theme={theme} issues={issues} services={services} dispatch={dispatch} />
        )}
      </aside>

      {dialog?.kind === 'saveAs' && (
        <SaveAsDialog
          initial={current.builtin ? `${theme.name} (mío)` : `${theme.name} (copia)`}
          reason={dialog.reason}
          note={saveAsNote(current.builtin, theme.name, status.kind)}
          onCancel={() => setDialog(null)}
          onSave={(t) => void saveAs(t, dialog.thenApply)}
        />
      )}
      {dialog?.kind === 'settings' && (
        <SettingsDialog
          api={api}
          toast={toast}
          onDisplayChosen={() => {
            // La prueba tarda un poco: se mira ahora y otra vez al rato.
            refreshInfo()
            window.setTimeout(refreshInfo, 1500)
          }}
          onClose={() => setDialog(null)}
          onImported={(name) => {
            setDialog(null)
            void api
              .listThemes()
              .then(setThemes)
              .then(() => selectTheme(name))
              .catch((e: unknown) => toast(errorMessage(e), 'error'))
          }}
        />
      )}
      {info?.question && (
        <Dialog
          title={info.question.title}
          onClose={() => void answerQuestion(false)}
          actions={
            <>
              <button type="button" className="btn primary" disabled={answering} onClick={() => void answerQuestion(false)}>
                No
              </button>
              <button type="button" className="btn" disabled={answering} onClick={() => void answerQuestion(true)}>
                Sí, se ve bien
              </button>
            </>
          }
        >
          <p className="settings-confirm">{info.question.text}</p>
        </Dialog>
      )}
      {dialog?.kind === 'restore' && (
        <Dialog
          title="¿Restaurar el tema?"
          onClose={() => setDialog(null)}
          actions={
            <>
              <button type="button" className="btn" onClick={() => setDialog(null)}>
                Cancelar
              </button>
              <button type="button" className="btn primary" onClick={restore}>
                Restaurar
              </button>
            </>
          }
        >
          <p>Vuelve a como estaba al abrirlo. Si te arrepientes, Ctrl+Z lo deshace.</p>
        </Dialog>
      )}
      {dialog?.kind === 'applyFirst' && (
        <Dialog
          title={APPLY_BLOCK_TEXT[dialog.why].title}
          onClose={() => setDialog(null)}
          actions={
            <>
              <button type="button" className="btn" onClick={() => setDialog(null)}>
                Cancelar
              </button>
              {dialog.why === 'builtin-changed' && (
                <button type="button" className="btn" onClick={() => void applyNow(current.name, savedRef.current ?? theme)}>
                  Aplicar el original
                </button>
              )}
              {dialog.why === 'invalid' && (
                <button type="button" className="btn" onClick={() => void applyNow(current.name, savedRef.current ?? theme)}>
                  Aplicar lo último guardado
                </button>
              )}
              {dialog.why === 'builtin-changed' ? (
                <button type="button" className="btn primary" onClick={() => setDialog({ kind: 'saveAs', thenApply: true })}>
                  Guardar como… y aplicar
                </button>
              ) : dialog.why === 'pending' ? (
                <button type="button" className="btn primary" onClick={() => void applyNow(current.name, theme)}>
                  Guardar y aplicar
                </button>
              ) : null}
            </>
          }
        >
          <p>{APPLY_BLOCK_TEXT[dialog.why].body}</p>
        </Dialog>
      )}
      {dialog?.kind === 'removeWidget' && (
        <Dialog
          title="¿Borrar este elemento?"
          onClose={() => setDialog(null)}
          actions={
            <>
              <button type="button" className="btn" onClick={() => setDialog(null)}>
                Cancelar
              </button>
              <button
                type="button"
                className="btn danger solid"
                onClick={() => {
                  removeWidget(dialog.id)
                  setDialog(null)
                }}
              >
                Borrar
              </button>
            </>
          }
        >
          <p>
            Se quitará «{labels[ids.indexOf(dialog.id)] ?? 'elemento'}» de la pantalla. Puedes recuperarlo con Deshacer (Ctrl+Z).
          </p>
        </Dialog>
      )}
      {dialog?.kind === 'deleteTheme' && (
        <Dialog
          title={`¿Mover «${theme.name}» a la papelera?`}
          onClose={() => setDialog(null)}
          actions={
            <>
              <button type="button" className="btn" onClick={() => setDialog(null)}>
                Cancelar
              </button>
              <button type="button" className="btn danger solid" onClick={() => void deleteTheme(dialog.onScreen)}>
                Mover a la papelera
              </button>
            </>
          }
        >
          <p>Desaparece de la lista, pero no se borra del todo: queda en la carpeta «papelera» de Luzeira por si lo quieres recuperar.</p>
          {dialog.onScreen && <p>Es el tema que está ahora en la pantalla: se pondrá «Esencial» en su lugar.</p>}
        </Dialog>
      )}
      {dialog?.kind === 'discard' && (
        <Dialog
          title="¿Dejar los cambios sin guardar?"
          onClose={() => setDialog(null)}
          actions={
            <>
              <button type="button" className="btn" onClick={() => setDialog({ kind: 'saveAs' })}>
                Guardar como…
              </button>
              <button
                type="button"
                className="btn danger solid"
                onClick={() => {
                  const next = dialog.next
                  setDialog(null)
                  void loadTheme(next)
                }}
              >
                Descartar cambios
              </button>
            </>
          }
        >
          <p>Has cambiado un tema incluido y esos cambios no se pueden guardar en él. Si sales ahora se pierden.</p>
        </Dialog>
      )}
      <Toasts items={toasts} onDismiss={(id) => setToasts((t) => t.filter((x) => x.id !== id))} />
      <Tooltips />
    </div>
  )
}

function SaveAsDialog({
  initial,
  reason,
  note,
  onCancel,
  onSave,
}: {
  initial: string
  reason?: string
  note: string
  onCancel: () => void
  onSave: (title: string) => void
}) {
  const [title, setTitle] = useState(initial.slice(0, LIMITS.nameMax))
  const clean = title.trim()
  const valid = clean.length >= 1 && clean.length <= LIMITS.nameMax
  return (
    <Dialog
      title="Guardar como…"
      onClose={onCancel}
      actions={
        <>
          <button type="button" className="btn" onClick={onCancel}>
            Cancelar
          </button>
          <button type="button" className="btn primary" disabled={!valid} onClick={() => onSave(clean)}>
            Guardar en «Mis temas»
          </button>
        </>
      }
    >
      <p>{reason ?? note}</p>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (valid) onSave(clean)
        }}
      >
        <div className="field">
          <label htmlFor="save-as-title">
            <span>Nombre del tema nuevo</span>
            <span className="counter">
              {title.length}/{LIMITS.nameMax}
            </span>
          </label>
          <input id="save-as-title" className="input" maxLength={LIMITS.nameMax} value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
      </form>
    </Dialog>
  )
}

// --- Utilidades ---------------------------------------------------------------------

/**
 * Texto de «Guardar como…». En un tema incluido el original no cambia; en uno del
 * usuario el guardado automático ya ha escrito los cambios en él, así que decir «el
 * original no cambia» sería falso.
 */
function saveAsNote(builtin: boolean, title: string, status: SaveStatus['kind']): string {
  if (builtin) return 'Se crea una copia con sus imágenes en «Mis temas». El tema incluido no cambia.'
  if (status === 'saved' || status === 'pending' || status === 'saving') {
    return `Tus cambios ya están guardados en «${title}». Esto crea una copia nueva con sus imágenes.`
  }
  return `Se crea una copia nueva con sus imágenes y con lo que ves ahora. «${title}» se queda con lo último que se guardó.`
}

function widgetLabel(w: Widget, sensors: SensorInfo[]): string {
  const sensorName = (id: string) => sensors.find((s) => s.id === id)?.label ?? id
  switch (w.type) {
    case 'text':
      return w.text.trim() ? `«${w.text.trim().slice(0, 40)}»` : '(texto vacío)'
    case 'clock':
      return `${CLOCK_FORMAT_INFO[w.format]} · ${formatClock(w.format, new Date())}`
    case 'sensor':
      return sensorName(w.sensor)
    case 'bar':
      return `Barra · ${sensorName(w.sensor)}`
    case 'image':
      return baseName(w.file)
  }
}

function dedupe(list: Issue[]): Issue[] {
  const seen = new Set<string>()
  return list.filter((i) => {
    const k = `${i.path}|${i.message}`
    if (seen.has(k)) return false
    seen.add(k)
    return true
  })
}

/** Abre el selector de ficheros del sistema. Resuelve null si se cancela. */
function pickFile(accept: string): Promise<File | null> {
  return new Promise((resolve) => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = accept
    input.addEventListener('change', () => resolve(input.files?.[0] ?? null), { once: true })
    input.addEventListener('cancel', () => resolve(null), { once: true })
    input.click()
  })
}

async function imageSize(file: File): Promise<{ width: number; height: number } | null> {
  try {
    const bmp = await createImageBitmap(file)
    const size = { width: bmp.width, height: bmp.height }
    bmp.close()
    return size
  } catch {
    return null
  }
}
