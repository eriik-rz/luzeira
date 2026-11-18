// API simulada: todo en memoria, sin servidor. Imita las respuestas y los errores del
// contrato (incluidos los 400 con `details`) para que el editor se comporte igual.
// La vista previa se dibuja aproximada en un <canvas>: NO es la de Python.

import { ApiError, type EditorApi, type Info, type SensorInfo, type ThemeDocument, type ThemeSummary } from '../api.ts'
import { formatClock, formatReading } from '../model/format.ts'
import { normalizeTheme } from '../model/theme.ts'
import { screenSize, type Theme, type ThemeJson } from '../model/types.ts'
import { validateTheme } from '../model/validate.ts'
import { ESENCIAL, MI_TEMA, readSimSensors } from './simData.ts'

interface StoredTheme {
  builtin: boolean
  theme: ThemeJson
  files: Map<string, Blob>
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

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
    ['mi-tema', { builtin: false, theme: MI_TEMA, files: new Map() }],
  ])
  private readings: SensorInfo[] = readSimSensors()
  private applied = 'esencial'

  private get(name: string): StoredTheme {
    const t = this.themes.get(name)
    if (!t) throw new ApiError('Ese tema ya no existe.', 404)
    return t
  }

  async info(): Promise<Info> {
    await wait(50)
    return {
      version: '0.0.0-simulado',
      status: 'simulado',
      message: 'Modo simulado: no hay pantalla conectada, los datos son inventados.',
      active_theme: this.applied,
      simulated: true,
    }
  }

  async listThemes(): Promise<ThemeSummary[]> {
    await wait(50)
    return [...this.themes].map(([name, t]) => ({ name, title: t.theme.name, builtin: t.builtin }))
  }

  async getTheme(name: string): Promise<ThemeDocument> {
    await wait(80)
    const t = this.get(name)
    return { name, builtin: t.builtin, theme: structuredClone(t.theme), files: [...t.files.keys()] }
  }

  async saveTheme(name: string, theme: Theme): Promise<void> {
    await wait(250)
    const t = this.get(name)
    if (t.builtin) throw new ApiError('Los temas incluidos no se pueden cambiar. Usa «Guardar como…».', 409)
    const issues = validateTheme(theme)
    if (issues.length) throw new ApiError('El tema tiene errores; revisa los campos marcados.', 400, issues)
    t.theme = structuredClone(theme)
  }

  async copyTheme(name: string, title: string, theme?: Theme): Promise<string> {
    await wait(150)
    const t = this.get(name)
    const clean = title.trim()
    if (!clean || clean.length > 60) {
      throw new ApiError('El nombre tiene que tener entre 1 y 60 caracteres.', 400, [{ path: 'title', message: 'Entre 1 y 60 caracteres.' }])
    }
    // Con `theme`, la copia lleva ya lo editado (un solo paso), validado como en PUT.
    const content: ThemeJson = theme === undefined ? structuredClone(t.theme) : structuredClone(theme)
    if (theme !== undefined) {
      const issues = validateTheme({ ...theme, name: clean })
      if (issues.length) throw new ApiError('El tema tiene errores; revisa los campos marcados.', 400, issues)
    }
    let slug = slugify(clean)
    for (let i = 2; this.themes.has(slug); i++) slug = `${slugify(clean)}-${i}`
    this.themes.set(slug, { builtin: false, theme: { ...content, name: clean }, files: new Map(t.files) })
    return slug
  }

  async deleteTheme(name: string): Promise<void> {
    await wait(100)
    const t = this.get(name)
    if (t.builtin) throw new ApiError('Los temas incluidos no se pueden borrar.', 409)
    if (name === this.applied) throw new ApiError('Este tema está en la pantalla. Aplica otro antes de borrarlo.', 409)
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
    if (t.builtin) throw new ApiError('Los temas incluidos no se pueden cambiar. Usa «Guardar como…».', 409)
    if (file.size > 10 * 1024 * 1024) throw new ApiError('El fichero pasa de 10 MB.', 413)
    const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
    const isFont = ext === 'ttf' || ext === 'otf'
    if (!['png', 'jpg', 'jpeg', 'gif', 'ttf', 'otf'].includes(ext)) {
      throw new ApiError('Solo se admiten imágenes PNG, JPG o GIF y fuentes TTF u OTF.', 400)
    }
    const stem = slugify(file.name.replace(/\.[^.]+$/, '')).slice(0, 40) || 'fichero'
    let path = `${isFont ? 'fuentes' : 'img'}/${stem}.${ext}`
    for (let i = 2; t.files.has(path); i++) path = `${isFont ? 'fuentes' : 'img'}/${stem}-${i}.${ext}`
    t.files.set(path, file)
    return path
  }

  async resource(name: string, path: string): Promise<Blob> {
    const blob = this.get(name).files.get(path)
    if (!blob) throw new ApiError('No se ha encontrado el fichero.', 404)
    return blob
  }

  async preview(name: string, theme: Theme, signal?: AbortSignal): Promise<Blob> {
    await wait(120)
    if (signal?.aborted) throw new DOMException('Cancelada', 'AbortError')
    const issues = validateTheme(theme)
    if (issues.length) throw new ApiError('El tema tiene errores; revisa los campos marcados.', 400, issues)
    return renderSimulated(normalizeTheme(theme), this.get(name).files, this.readings)
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

async function renderSimulated(theme: Theme, files: Map<string, Blob>, readings: SensorInfo[]): Promise<Blob> {
  const { width, height } = screenSize(theme)
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new ApiError('Este navegador no puede dibujar la vista previa simulada.', 0)

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
          ? w.text
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
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new ApiError('No se pudo crear la vista previa.', 0))), 'image/png'),
  )
}
