// API simulada: todo en memoria, sin servidor. Imita las respuestas y los errores del
// contrato (incluidos los 400 con `details`) para que el editor se comporte igual.
// La vista previa se dibuja aproximada en un <canvas>: NO es la de Python.

import { ApiError, type AppSettings, type DisplayOption, type EditorApi, type Info, type MissingGlyphs, type Preview, type SettingsChange, type SensorInfo, type ThemeDocument, type ThemeSummary } from '../api.ts'
import { availableLanguages, NATIVE_NAMES, resolveLanguage, t } from '../model/i18n.ts'
import { formatClock, formatReading, localized } from '../model/format.ts'
import { normalizeTheme } from '../model/theme.ts'
import { screenSize, type Theme, type ThemeJson } from '../model/types.ts'
import { validateTheme } from '../model/validate.ts'
import { ESENCIAL, inLanguage, miTema, readSimSensors } from './simData.ts'

/** Los ajustes que guarda la demo (lo de la pantalla se calcula al pedirlos). */
type StoredSettings = Omit<AppSettings, 'display_text' | 'display_auto' | 'choices' | 'advanced'>

/** `t` con otro nombre, para los métodos que ya llaman `t` al tema que tocan. */
const t_ = t

interface StoredTheme {
  builtin: boolean
  theme: ThemeJson
  files: Map<string, Blob>
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

/**
 * Temas incluidos de verdad, para la demo de la web: `scripts/web.py` copia
 * `src/luzeira/recursos/temas` junto al editor y escribe `temas/temas.json` (lista de
 * carpetas). Sin esa lista (con `npm run dev`), la demo se queda con los de `simData`.
 */
const BUNDLED_INDEX = 'temas/temas.json'

/** Ficheros (imágenes) que usa un tema: el fondo y los elementos de imagen. */
function themeFiles(theme: ThemeJson): string[] {
  const out = new Set<string>()
  if (theme.background?.image) out.add(theme.background.image)
  for (const w of theme.widgets ?? []) if (w.type === 'image' && w.file) out.add(w.file)
  return [...out]
}

/**
 * `?lang=en` en la dirección fuerza el idioma de la demo (para capturas); sin él, manda el
 * del navegador («automático»).
 */
function languageFromUrl(): string {
  const code = typeof location === 'undefined' ? null : new URLSearchParams(location.search).get('lang')
  return code && availableLanguages().includes(code) ? code : 'auto'
}

function slugify(title: string): string {
  const base = title
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return base || 'tema'
}

export class SimulatedApi implements EditorApi {
  readonly simulated = true
  private themes = new Map<string, StoredTheme>([
    ['esencial', { builtin: true, theme: ESENCIAL, files: new Map() }],
    ['mi-tema', { builtin: false, theme: inLanguage(resolveLanguage(languageFromUrl(), navigator.language), miTema), files: new Map() }],
  ])
  private readings: SensorInfo[] = readSimSensors()
  private applied = 'esencial'
  private ready: Promise<void> = this.loadBundled()
  /** Ficheros de los temas incluidos que aún no se han descargado. */
  private pendingFiles = new Map<string, string[]>()

  /** Carga los temas incluidos de verdad si la web los trae (ver `BUNDLED_INDEX`). */
  private async loadBundled(): Promise<void> {
    let names: string[]
    try {
      const res = await fetch(BUNDLED_INDEX, { cache: 'no-store' })
      if (!res.ok) return
      names = (await res.json()) as string[]
    } catch {
      return
    }
    const loaded = await Promise.all(
      names.map(async (name) => {
        try {
          // no-cache: tras publicar la web, el navegador pregunta antes de reusar un tema viejo.
          const res = await fetch(`temas/${encodeURIComponent(name)}/theme.json`, {
            cache: 'no-cache',
          })
          return res.ok ? ([name, (await res.json()) as ThemeJson] as const) : null
        } catch {
          return null
        }
      }),
    )
    const mine = [...this.themes].filter(([, t]) => !t.builtin)
    const builtins = loaded.filter((x) => x !== null)
    if (!builtins.length) return
    this.themes = new Map([
      ...builtins.map(([name, theme]) => [name, { builtin: true, theme, files: new Map<string, Blob>() }] as const),
      ...mine,
    ])
    for (const [name, theme] of builtins) this.pendingFiles.set(name, themeFiles(theme))
  }

  /** Descarga (una vez) las imágenes de un tema incluido antes de enseñarlo. */
  private async ensureFiles(name: string): Promise<void> {
    await this.ready
    const pending = this.pendingFiles.get(name)
    if (!pending) return
    this.pendingFiles.delete(name)
    const files = this.get(name).files
    await Promise.all(
      pending.map(async (path) => {
        try {
          const safe = path.split('/').map(encodeURIComponent).join('/')
          const res = await fetch(`temas/${encodeURIComponent(name)}/${safe}`, {
            cache: 'no-cache',
          })
          if (res.ok) files.set(path, await res.blob())
        } catch {
          // sin la imagen, la vista previa pinta solo el color de fondo
        }
      }),
    )
  }

  private get(name: string): StoredTheme {
    const t = this.themes.get(name)
    if (!t) throw new ApiError(t_('demo.error.no_existe'), 404)
    return t
  }

  async info(): Promise<Info> {
    await wait(50)
    // `?sin-pantalla`: el aviso de la app real cuando no encuentra la pantalla (para verlo
    // y hacer capturas sin desenchufar nada).
    const noDisplay = typeof location !== 'undefined' && new URLSearchParams(location.search).has('sin-pantalla')
    return {
      version: '0.0.0-simulado',
      // En la demo, «automático» es el idioma del navegador.
      language: resolveLanguage(this.appSettings.language, navigator.language),
      status: noDisplay ? 'no_display' : 'simulado',
      message: noDisplay ? t('demo.info.sin_pantalla') : t('demo.info.simulado'),
      active_theme: this.applied,
      simulated: true,
      question: this.question(),
      // En la demo de la web no se pregunta nada al entrar: se ve en «Ajustes».
      updates_question: false,
      update_available: null,
    }
  }

  async listThemes(): Promise<ThemeSummary[]> {
    await this.ready
    await wait(50)
    return [...this.themes].map(([name, t]) => ({ name, title: t.theme.name, builtin: t.builtin }))
  }

  async getTheme(name: string): Promise<ThemeDocument> {
    await this.ensureFiles(name)
    await wait(80)
    const t = this.get(name)
    return { name, builtin: t.builtin, theme: structuredClone(t.theme), files: [...t.files.keys()] }
  }

  async saveTheme(name: string, theme: Theme): Promise<void> {
    await wait(250)
    const t = this.get(name)
    if (t.builtin) throw new ApiError(t_('demo.error.incluido'), 409)
    const issues = validateTheme(theme)
    if (issues.length) throw new ApiError(t_('demo.error.errores'), 400, issues)
    t.theme = structuredClone(theme)
  }

  async copyTheme(name: string, title: string, theme?: Theme): Promise<string> {
    await this.ensureFiles(name)
    await wait(150)
    const t = this.get(name)
    const clean = title.trim()
    if (!clean || clean.length > 60) {
      throw new ApiError(t_('demo.error.nombre'), 400, [{ path: 'title', message: t_('demo.error.nombre.corto') }])
    }
    // Con `theme`, la copia lleva ya lo editado (un solo paso), validado como en PUT.
    const content: ThemeJson = theme === undefined ? structuredClone(t.theme) : structuredClone(theme)
    if (theme !== undefined) {
      const issues = validateTheme({ ...theme, name: clean })
      if (issues.length) throw new ApiError(t_('demo.error.errores'), 400, issues)
    }
    let slug = slugify(clean)
    for (let i = 2; this.themes.has(slug); i++) slug = `${slugify(clean)}-${i}`
    this.themes.set(slug, { builtin: false, theme: { ...content, name: clean }, files: new Map(t.files) })
    return slug
  }

  async deleteTheme(name: string): Promise<void> {
    await wait(100)
    const t = this.get(name)
    if (t.builtin) throw new ApiError(t_('demo.error.borrar_incluido'), 409)
    if (name === this.applied) throw new ApiError(t_('demo.error.en_pantalla'), 409)
    this.themes.delete(name)
  }

  async sensors(): Promise<SensorInfo[]> {
    await wait(30)
    this.readings = readSimSensors()
    return this.readings
  }

  async apply(name: string): Promise<void> {
    await wait(300)
    this.get(name)
    this.applied = name
  }

  async uploadResource(name: string, file: File): Promise<string> {
    await wait(200)
    const t = this.get(name)
    if (t.builtin) throw new ApiError(t_('demo.error.incluido'), 409)
    if (file.size > 10 * 1024 * 1024) throw new ApiError(t_('demo.error.grande'), 413)
    const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
    const isFont = ext === 'ttf' || ext === 'otf'
    if (!['png', 'jpg', 'jpeg', 'gif', 'ttf', 'otf'].includes(ext)) {
      throw new ApiError(t_('demo.error.tipo'), 400)
    }
    const stem = slugify(file.name.replace(/\.[^.]+$/, '')).slice(0, 40) || 'fichero'
    let path = `${isFont ? 'fuentes' : 'img'}/${stem}.${ext}`
    for (let i = 2; t.files.has(path); i++) path = `${isFont ? 'fuentes' : 'img'}/${stem}-${i}.${ext}`
    t.files.set(path, file)
    return path
  }

  /**
   * Lo que se guarda de los ajustes. La pantalla elegida y la lista de pantallas van aparte
   * (`chosen`, `displayOptions`): sus textos se escriben en el idioma activo cada vez que se
   * piden, para que cambiar de idioma en la demo los cambie también.
   */
  private appSettings: StoredSettings = {
    updates: { can_check: true, enabled: null, available: null, busy: false, version: '0.0.0-simulado' },
    brightness: 50,
    brightness_levels: [25, 50, 75, 100],
    flip: false,
    store: false,
    autostart: true,
    autostart_blocked: false,
    language: languageFromUrl(),
    languages: availableLanguages().map((code) => ({ code, name: NATIVE_NAMES[code] })),
  }
  /** La pantalla elegida a mano (`null`: «detectarla sola»). */
  private chosen: string | null = null

  private displayOptions(): Pick<AppSettings, 'choices' | 'advanced'> {
    const option = (id: string, label: string, beta: boolean, warning: string | null): DisplayOption => ({
      id,
      label,
      beta,
      current: id === this.chosen,
      warning,
    })
    return {
      choices: [
        option('lista:COM3:turing-rev-a', t('demo.pantalla.turing', { puerto: 'COM3' }), false, null),
        option('lista:COM8:kipye-rev-d', t('demo.pantalla.kipye'), true, t('demo.pantalla.kipye.aviso')),
      ],
      advanced: [option('avanzado:COM4:turing-rev-a', t('demo.pantalla.bluetooth'), false, t('demo.pantalla.avanzado.aviso'))],
    }
  }

  private findDisplay(id: string | null): DisplayOption | undefined {
    const { choices, advanced } = this.displayOptions()
    return [...choices, ...advanced].find((o) => o.id === id)
  }

  async settings(): Promise<AppSettings> {
    await wait(50)
    const option = this.findDisplay(this.chosen)
    return {
      ...structuredClone(this.appSettings),
      ...this.displayOptions(),
      display_text: option ? t('demo.pantalla.probando', { pantalla: option.label }) : t('demo.pantalla.ninguna'),
      display_auto: this.chosen === null,
    }
  }

  async updateSettings(change: SettingsChange): Promise<void> {
    await wait(80)
    if (change.brightness !== undefined && !this.appSettings.brightness_levels.includes(change.brightness)) {
      throw new ApiError(t('demo.error.brillo'), 400)
    }
    const { updates, ...rest } = change
    this.appSettings = { ...this.appSettings, ...rest }
    if (updates !== undefined) this.appSettings.updates = { ...this.appSettings.updates, enabled: updates }
  }

  async checkUpdates(): Promise<string> {
    await wait(400)
    return t('demo.actualizar.ultima')
  }

  async installUpdate(): Promise<string> {
    await wait(80)
    throw new ApiError(t('demo.actualizar.ninguna'), 409)
  }

  async chooseDisplay(id: string | null, confirmed: boolean): Promise<void> {
    await wait(80)
    const option = id === null ? null : this.findDisplay(id)
    if (id !== null && !option) throw new ApiError(t('demo.error.pantalla_no_esta'), 409)
    if (option?.warning && !confirmed) throw new ApiError(t('demo.error.confirmar'), 400)
    this.chosen = id
    this.asking = option?.id?.includes('kipye') ? option.id : null
  }

  /** La Kipye (sin saludo) pregunta «¿se ve bien?» al elegirla, como en la app. */
  private asking: string | null = null

  private question(): Info['question'] {
    return this.asking ? { title: t('demo.pregunta.titulo'), text: t('demo.pregunta.texto') } : null
  }

  async answerQuestion(looksRight: boolean): Promise<string> {
    await wait(80)
    const asked = this.asking
    if (!asked) throw new ApiError(t('demo.error.pregunta'), 409)
    this.asking = null
    if (!looksRight) {
      this.chosen = null
      return t('demo.pregunta.no')
    }
    return t('demo.pregunta.si', { pantalla: this.findDisplay(asked)?.label ?? t('demo.pregunta.tu_pantalla') })
  }

  async createShortcut(): Promise<string> {
    await wait(100)
    return t('demo.acceso')
  }

  async openThemesFolder(): Promise<void> {
    await wait(50)
  }

  async openStartupSettings(): Promise<void> {
    await wait(50)
  }

  async importTheme(file: File): Promise<string> {
    await wait(200)
    if (!file.name.toLowerCase().endsWith('.zip')) throw new ApiError(t('demo.error.zip'), 400)
    const name = slugify(file.name.replace(/\.zip$/i, ''))
    this.themes.set(name, { builtin: false, theme: { ...miTema(), name: file.name.replace(/\.zip$/i, '') }, files: new Map() })
    return name
  }

  async resource(name: string, path: string): Promise<Blob> {
    const blob = this.get(name).files.get(path)
    if (!blob) throw new ApiError(t('demo.error.fichero'), 404)
    return blob
  }

  async preview(name: string, theme: Theme, signal?: AbortSignal): Promise<Preview> {
    await wait(120)
    if (signal?.aborted) throw new DOMException('Cancelada', 'AbortError')
    const issues = validateTheme(theme)
    if (issues.length) throw new ApiError(t('demo.error.errores'), 400, issues)
    const image = await renderSimulated(normalizeTheme(theme), this.get(name).files, this.readings)
    return { image, missingGlyphs: simMissingGlyphs(theme) }
  }

  /** Solo para depurar en la consola. */
  get appliedTheme() {
    return this.applied
  }
}

// --- Dibujo aproximado ---------------------------------------------------------------

async function bitmap(files: Map<string, Blob>, path: string | null): Promise<ImageBitmap | null> {
  const blob = path ? files.get(path) : undefined
  if (!blob) return null
  try {
    return await createImageBitmap(blob)
  } catch {
    return null
  }
}

/**
 * Imitación de las letras que faltan: aquí no hay fuentes de verdad. Se imita a Inter
 * (la fuente incluida), medida con `missing_glyphs`: tiene el latín, flechas, ★ y ✓,
 * pero no los símbolos varios (☃, ⚡) ni los emojis.
 */
const SIM_FONT_EXTRAS = new Set(['★', '✓'])
const simFontHas = (c: string) => (c.codePointAt(0) ?? 0) < 0x2600 || SIM_FONT_EXTRAS.has(c)

function simMissingGlyphs(theme: Theme): MissingGlyphs[] {
  const out: MissingGlyphs[] = []
  theme.widgets.forEach((w, index) => {
    const text =
      w.type === 'text'
        ? localized(w.text, w.text_i18n)
        : w.type === 'sensor'
          ? `${localized(w.prefix, w.prefix_i18n)}${localized(w.suffix, w.suffix_i18n)}`
          : ''
    const missing = [...new Set([...text].filter((c) => !/\s/u.test(c) && !simFontHas(c)))]
    if (missing.length) out.push({ index, chars: missing.join('') })
  })
  return out
}

async function renderSimulated(theme: Theme, files: Map<string, Blob>, readings: SensorInfo[]): Promise<Blob> {
  const { width, height } = screenSize(theme)
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new ApiError(t('demo.error.canvas'), 0)

  ctx.fillStyle = theme.background.color
  ctx.fillRect(0, 0, width, height)
  const bg = await bitmap(files, theme.background.image)
  if (bg) {
    // «Cubrir»: escala hasta llenar y recorta, como _fit_cover en Python.
    const s = Math.max(width / bg.width, height / bg.height)
    ctx.drawImage(bg, (width - bg.width * s) / 2, (height - bg.height * s) / 2, bg.width * s, bg.height * s)
  }

  const now = new Date()
  const byId = new Map(readings.map((r) => [r.id, r]))
  for (const w of theme.widgets) {
    const b = w.box
    ctx.save()
    ctx.beginPath()
    ctx.rect(b.x, b.y, b.width, b.height)
    ctx.clip()
    if (w.style.background) {
      ctx.fillStyle = w.style.background
      ctx.fillRect(b.x, b.y, b.width, b.height)
    }
    if (w.type === 'bar') {
      const r = byId.get(w.sensor)
      if (!r || r.value === null) {
        ctx.strokeStyle = w.track
        ctx.strokeRect(b.x + 0.5, b.y + 0.5, b.width - 1, b.height - 1)
      } else {
        ctx.fillStyle = w.track
        ctx.fillRect(b.x, b.y, b.width, b.height)
        const ratio = Math.min(1, Math.max(0, (r.value - w.min) / (w.max - w.min)))
        ctx.fillStyle = w.fill
        if (w.direction === 'horizontal') ctx.fillRect(b.x, b.y, Math.round(b.width * ratio), b.height)
        else {
          const h = Math.round(b.height * ratio)
          ctx.fillRect(b.x, b.y + b.height - h, b.width, h)
        }
      }
    } else if (w.type === 'image') {
      const img = await bitmap(files, w.file)
      if (img) {
        const s = Math.min(1, b.width / img.width, b.height / img.height)
        const iw = img.width * s
        const ih = img.height * s
        ctx.drawImage(img, b.x + (b.width - iw) / 2, b.y + (b.height - ih) / 2, iw, ih)
      } else {
        ctx.strokeStyle = '#8a93a6'
        ctx.setLineDash([4, 3])
        ctx.strokeRect(b.x + 0.5, b.y + 0.5, b.width - 1, b.height - 1)
      }
    } else {
      const text =
        w.type === 'text'
          ? localized(w.text, w.text_i18n) // el texto del tema en el idioma activo, si lo trae
          : w.type === 'clock'
            ? formatClock(w.format, now)
            : formatReading(w, byId.get(w.sensor))
      ctx.font = `${w.style.bold ? 'bold ' : ''}${w.style.size}px "Segoe UI", system-ui, sans-serif`
      ctx.fillStyle = w.style.color
      ctx.textBaseline = 'middle'
      ctx.textAlign = w.style.align
      const x = w.style.align === 'left' ? b.x : w.style.align === 'right' ? b.x + b.width : b.x + b.width / 2
      ctx.fillText(text, x, b.y + b.height / 2)
    }
    ctx.restore()
  }

  return new Promise((resolve, reject) =>
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new ApiError(t('demo.error.vista_previa'), 0))), 'image/png'),
  )
}
