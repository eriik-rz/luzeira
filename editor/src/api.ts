// Capa de API: habla con el servidor local de Luzeira (docs/planning/fase-5-editor.md).
//
// Seguridad (RQ-14):
//  - El token NUNCA va en la URL de las peticiones: viaja en la cabecera X-Luzeira-Token.
//  - En la ventana de Luzeira (pywebview) el token se pide al puente JS:
//    `await window.pywebview.api.token()`.
//  - En desarrollo (`npm run dev` en un navegador) se lee del fragmento `#token=…`, que
//    el navegador no envía al servidor.
//
// Modo simulado (`?simulado` en la dirección o VITE_SIMULADO=1): datos falsos en memoria
// para desarrollar y hacer capturas sin el servidor (src/sim/).

import { encodeFilenameHeader, FILENAME_HEADER, isSupersededPreview, messageForStatus } from './model/contract.ts'
import type { Theme, ThemeJson } from './model/types.ts'

// --- Tipos de las respuestas ---------------------------------------------------------

export interface Info {
  version: string
  status: string
  message: string
  /** Tema que está en la pantalla ahora (para abrir el editor con él). */
  active_theme?: string | null
  simulated?: boolean
  /** «¿Se ve bien?» de una pantalla elegida en «Ajustes», esperando respuesta. */
  question?: { title: string; text: string } | null
}

export interface ThemeSummary {
  name: string // nombre de la carpeta (identificador)
  title: string // theme.name, lo que ve el usuario
  builtin: boolean
}

export interface ThemeDocument {
  name: string
  builtin: boolean
  theme: ThemeJson
  files: string[]
}

export interface SensorInfo {
  id: string
  label: string
  unit: string
  category: string
  kind: 'number' | 'text'
  value: number | null
  text: string | null
  reason: string | null
}

/** Una opción de «Modelo y puerto». `id` es lo que se manda de vuelta al elegirla. */
export interface DisplayOption {
  /** `null`: se ve pero no se puede elegir (p. ej. la revisión C). */
  id: string | null
  label: string
  beta: boolean
  current: boolean
  /** Aviso que hay que aceptar antes de probarla (beta o modo avanzado). */
  warning: string | null
}

/** Ajustes de la app (lo mismo que el menú del icono). */
export interface AppSettings {
  brightness: number
  brightness_levels: number[]
  flip: boolean
  autostart: boolean
  display_text: string
  /** Luzeira busca la pantalla ella sola (no hay ninguna elegida a mano). */
  display_auto: boolean
  choices: DisplayOption[]
  advanced: DisplayOption[]
}

export type SettingsChange = Partial<Pick<AppSettings, 'brightness' | 'flip' | 'autostart'>>

export interface ErrorDetail {
  path: string
  message: string
}

/** Error de la API con el mensaje en español del servidor y los fallos por campo. */
export class ApiError extends Error {
  readonly status: number
  readonly details: ErrorDetail[]
  /** Vista previa superada por otra más nueva: no es un error para el usuario. */
  readonly superseded: boolean
  constructor(message: string, status: number, details: ErrorDetail[] = [], superseded = false) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.details = details
    this.superseded = superseded
  }
}

export interface EditorApi {
  readonly simulated: boolean
  info(): Promise<Info>
  listThemes(): Promise<ThemeSummary[]>
  getTheme(name: string): Promise<ThemeDocument>
  /**
   * Guarda el tema. Va con `keepalive`, para que el PUT llegue aunque la ventana se esté
   * cerrando (el navegador lo termina después de descargar la página).
   */
  saveTheme(name: string, theme: Theme): Promise<void>
  /**
   * «Guardar como» en un solo paso: copia `name` (carpeta y recursos) a un tema nuevo
   * del usuario y, si se pasa `theme`, guarda ya en la copia el tema editado.
   */
  copyTheme(name: string, title: string, theme?: Theme): Promise<string>
  deleteTheme(name: string): Promise<void>
  /**
   * PNG del tamaño real de la pantalla con el tema aún sin guardar. `null` si el
   * servidor la descarta porque ya llegó una petición más nueva (no es un error).
   */
  preview(name: string, theme: Theme, signal?: AbortSignal): Promise<Blob | null>
  sensors(): Promise<SensorInfo[]>
  apply(name: string): Promise<void>
  /** Sube una imagen o fuente; devuelve la ruta dentro del tema («img/logo.png»). */
  uploadResource(name: string, file: File): Promise<string>
  resource(name: string, path: string, signal?: AbortSignal): Promise<Blob>
  /** Ajustes de la app. 404 si el editor se abrió sin la bandeja (modo desarrollo). */
  settings(): Promise<AppSettings>
  updateSettings(change: SettingsChange): Promise<void>
  /** `null`: volver a detectarla sola. `confirmed`: el usuario ha aceptado el aviso. */
  chooseDisplay(id: string | null, confirmed: boolean): Promise<void>
  /** Contesta «¿se ve bien?»; devuelve el texto para el usuario. */
  answerQuestion(looksRight: boolean): Promise<string>
  /** Devuelve el texto para el usuario. */
  createShortcut(): Promise<string>
  openThemesFolder(): Promise<void>
  /** Importa un tema `.zip`; devuelve el nombre del tema nuevo. */
  importTheme(file: File): Promise<string>
}

// --- Token ---------------------------------------------------------------------------

interface PywebviewBridge {
  api?: { token?: () => Promise<string> }
}
declare global {
  interface Window {
    pywebview?: PywebviewBridge
  }
}

function tokenFromHash(): string | null {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ''))
  return params.get('token')
}

/**
 * pywebview inyecta `window.pywebview` cuando la página ya ha cargado y avisa con el
 * evento `pywebviewready`. Se espera un poco por él antes de rendirse.
 */
async function obtainToken(timeoutMs = 4000): Promise<string> {
  const fromBridge = async () => {
    const fn = window.pywebview?.api?.token
    return fn ? await fn() : null
  }
  const now = await fromBridge()
  if (now) return now
  // El fragmento solo se acepta en desarrollo (`npm run dev`): en la app compilada el
  // token llega siempre por el puente de pywebview y nunca pasa por una URL.
  const hash = import.meta.env.DEV ? tokenFromHash() : null
  if (hash) return hash
  const waited = await new Promise<string | null>((resolve) => {
    const timer = window.setTimeout(() => resolve(null), timeoutMs)
    window.addEventListener(
      'pywebviewready',
      () => {
        window.clearTimeout(timer)
        fromBridge().then(resolve, () => resolve(null))
      },
      { once: true },
    )
  })
  if (waited) return waited
  throw new ApiError(
    'No se pudo conectar con Luzeira. Abre el editor desde el icono de Luzeira en la bandeja.',
    0,
  )
}

// --- Cliente real --------------------------------------------------------------------

class HttpApi implements EditorApi {
  readonly simulated = false
  private readonly token: string
  constructor(token: string) {
    this.token = token
  }

  private async request(
    method: string,
    path: string,
    body?: BodyInit,
    contentType?: string,
    signal?: AbortSignal,
    extraHeaders: Record<string, string> = {},
    keepalive = false,
  ): Promise<Response> {
    const headers: Record<string, string> = { ...extraHeaders, 'X-Luzeira-Token': this.token }
    if (contentType) headers['Content-Type'] = contentType
    let res: Response
    try {
      res = await fetch(path, { method, headers, body, signal, keepalive, cache: 'no-store', credentials: 'omit' })
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') throw e
      throw new ApiError('Se ha perdido la conexión con Luzeira. ¿Sigue abierta?', 0)
    }
    if (!res.ok) throw await errorFrom(res)
    return res
  }

  private async json<T>(method: string, path: string, data?: unknown): Promise<T> {
    const res = await this.request(
      method,
      path,
      data === undefined ? undefined : JSON.stringify(data),
      data === undefined ? undefined : 'application/json',
    )
    try {
      return (await res.json()) as T
    } catch {
      throw new ApiError('Luzeira ha respondido algo inesperado. Cierra el editor y vuelve a abrirlo.', res.status)
    }
  }

  info() {
    return this.json<Info>('GET', '/api/info')
  }
  listThemes() {
    return this.json<ThemeSummary[]>('GET', '/api/temas')
  }
  getTheme(name: string) {
    return this.json<ThemeDocument>('GET', `/api/temas/${enc(name)}`)
  }
  async saveTheme(name: string, theme: Theme) {
    const body = JSON.stringify(theme)
    // `keepalive` admite como mucho 64 KB en vuelo; un tema pesa unos pocos KB, pero si
    // alguno se pasara, mejor guardarlo sin él que fallar.
    const keepalive = new TextEncoder().encode(body).length < KEEPALIVE_MAX
    await this.request('PUT', `/api/temas/${enc(name)}`, body, 'application/json', undefined, {}, keepalive)
  }
  async copyTheme(name: string, title: string, theme?: Theme) {
    const body = theme === undefined ? { title } : { title, theme }
    const r = await this.json<{ name: string }>('POST', `/api/temas/${enc(name)}/copiar`, body)
    return r.name
  }
  async deleteTheme(name: string) {
    await this.json('DELETE', `/api/temas/${enc(name)}`)
  }
  async preview(name: string, theme: Theme, signal?: AbortSignal) {
    try {
      const res = await this.request(
        'POST',
        '/api/vista-previa',
        JSON.stringify({ name, theme }),
        'application/json',
        signal,
      )
      if (isSupersededPreview(res.status)) return null
      return await res.blob()
    } catch (e) {
      if (e instanceof ApiError && e.superseded) return null
      throw e
    }
  }
  sensors() {
    return this.json<SensorInfo[]>('GET', '/api/sensores')
  }
  async apply(name: string) {
    await this.json('POST', `/api/temas/${enc(name)}/aplicar`, {})
  }
  async uploadResource(name: string, file: File) {
    // El nombre es solo una sugerencia: el servidor lo sanea y decide el definitivo.
    // Va en una cabecera (codificado en URL), no en la dirección.
    const res = await this.request(
      'POST',
      `/api/temas/${enc(name)}/recursos`,
      file,
      'application/octet-stream',
      undefined,
      { [FILENAME_HEADER]: encodeFilenameHeader(file.name) },
    )
    const data = (await res.json()) as { path: string }
    return data.path
  }
  async resource(name: string, path: string, signal?: AbortSignal) {
    const safe = path.split('/').map(encodeURIComponent).join('/')
    const res = await this.request('GET', `/api/temas/${enc(name)}/recursos/${safe}`, undefined, undefined, signal)
    return res.blob()
  }
  settings() {
    return this.json<AppSettings>('GET', '/api/ajustes')
  }
  async updateSettings(change: SettingsChange) {
    await this.json('PUT', '/api/ajustes', change)
  }
  async chooseDisplay(id: string | null, confirmed: boolean) {
    await this.json('POST', '/api/pantalla', id === null ? { auto: true } : { id, confirmed })
  }
  async answerQuestion(looksRight: boolean) {
    const r = await this.json<{ message: string }>('POST', '/api/pantalla/respuesta', { looks_right: looksRight })
    return r.message
  }
  async createShortcut() {
    const r = await this.json<{ message: string }>('POST', '/api/acceso-escritorio', {})
    return r.message
  }
  async openThemesFolder() {
    await this.json('POST', '/api/carpeta-temas', {})
  }
  async importTheme(file: File) {
    const res = await this.request('POST', '/api/importar-tema', file, 'application/zip', undefined, {
      [FILENAME_HEADER]: encodeFilenameHeader(file.name),
    })
    const data = (await res.json()) as { name: string }
    return data.name
  }
}

const enc = encodeURIComponent
/** Margen bajo el límite de 64 KB de `fetch(..., {keepalive: true})`. */
const KEEPALIVE_MAX = 60_000

async function errorFrom(res: Response): Promise<ApiError> {
  // 403 = seguridad (token, Host, Origin): sin detalles, mensaje genérico siempre.
  if (res.status === 403) return new ApiError(messageForStatus(403), 403)
  try {
    const data = (await res.json()) as { error?: string; details?: ErrorDetail[] }
    return new ApiError(
      messageForStatus(res.status, data.error),
      res.status,
      Array.isArray(data.details) ? data.details : [],
      isSupersededPreview(res.status, data),
    )
  } catch {
    return new ApiError(messageForStatus(res.status), res.status)
  }
}

// --- Elección de modo ----------------------------------------------------------------

export function isSimulatedRequested(): boolean {
  const params = new URLSearchParams(window.location.search)
  return params.has('simulado') || import.meta.env.VITE_SIMULADO === '1'
}

export async function connect(): Promise<EditorApi> {
  if (isSimulatedRequested()) {
    // Import dinámico: los datos falsos no entran en el paquete principal.
    const { SimulatedApi } = await import('./sim/simApi.ts')
    return new SimulatedApi()
  }
  return new HttpApi(await obtainToken())
}

/** Texto para el usuario de cualquier error. */
export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message
  return 'Algo ha fallado. Prueba otra vez.'
}
