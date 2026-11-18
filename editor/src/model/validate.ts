// Validación en el cliente: lo básico, para avisar al momento mientras se edita.
// La autoridad es el servidor (pydantic, modelo.py); aquí se repiten sus reglas con
// las mismas rutas que usan sus `details` («widgets.3.box.x») para pintarlas igual.

import {
  COLOR_RE,
  FONT_EXTENSIONS,
  IMAGE_EXTENSIONS,
  LIMITS,
  MAX_WIDGETS,
  REL_PATH_RE,
  SENSOR_ID_RE,
  screenSize,
  type Theme,
  type Widget,
} from './types.ts'

export interface Issue {
  path: string // «name», «widgets.3.box.x», «» (general)
  message: string
}

// Nombres reservados de Windows (los mismos que comprueba temas/rutas.py).
const RESERVED = /^(con|prn|aux|nul|com[0-9]|lpt[0-9])$/i

function checkPath(path: string, allowed: readonly string[]): string | null {
  if (path.length < 1 || path.length > LIMITS.pathMax || !REL_PATH_RE.test(path)) {
    return 'Nombre de fichero no válido.'
  }
  if (path.split('/').some((part) => RESERVED.test(part.split('.')[0]))) {
    return 'El fichero usa un nombre reservado de Windows (CON, NUL, COM1…).'
  }
  const ext = path.slice(path.lastIndexOf('.') + 1).toLowerCase()
  if (!allowed.includes(ext)) return `Extensión no permitida (solo ${allowed.join(', ')}).`
  return null
}

function range(
  out: Issue[],
  path: string,
  value: number,
  min: number,
  max: number,
  integer = true,
) {
  if (!Number.isFinite(value) || (integer && !Number.isInteger(value))) {
    out.push({ path, message: integer ? 'Tiene que ser un número entero.' : 'Tiene que ser un número.' })
  } else if (value < min || value > max) {
    out.push({ path, message: `Tiene que estar entre ${fmt(min)} y ${fmt(max)}.` })
  }
}

const fmt = (n: number) => String(n).replace('.', ',')

function color(out: Issue[], path: string, value: string | null) {
  if (value !== null && !COLOR_RE.test(value)) {
    out.push({ path, message: 'Color no válido (formato #rrggbb).' })
  }
}

function maxLen(out: Issue[], path: string, value: string, max: number) {
  if (value.length > max) out.push({ path, message: `Como mucho ${max} caracteres.` })
}

function sensorId(out: Issue[], path: string, value: string) {
  if (!SENSOR_ID_RE.test(value) || value.length > LIMITS.sensorIdMax) {
    out.push({ path, message: 'Elige un sensor de la lista.' })
  }
}

export function validateWidget(w: Widget, index: number, theme: Theme): Issue[] {
  const out: Issue[] = []
  const p = `widgets.${index}`
  const { width, height } = screenSize(theme)
  const b = w.box
  range(out, `${p}.box.x`, b.x, 0, LIMITS.boxCoordMax)
  range(out, `${p}.box.y`, b.y, 0, LIMITS.boxCoordMax)
  range(out, `${p}.box.width`, b.width, 1, LIMITS.boxCoordMax)
  range(out, `${p}.box.height`, b.height, 1, LIMITS.boxCoordMax)
  if (b.x + b.width > width) {
    out.push({ path: `${p}.box.width`, message: `Se sale por la derecha (la pantalla mide ${width} de ancho).` })
  }
  if (b.y + b.height > height) {
    out.push({ path: `${p}.box.height`, message: `Se sale por abajo (la pantalla mide ${height} de alto).` })
  }

  const s = w.style
  range(out, `${p}.style.size`, s.size, LIMITS.sizeMin, LIMITS.sizeMax)
  color(out, `${p}.style.color`, s.color)
  color(out, `${p}.style.background`, s.background)
  if (s.font !== null) {
    const err = checkPath(s.font, FONT_EXTENSIONS)
    if (err) out.push({ path: `${p}.style.font`, message: err })
  }

  switch (w.type) {
    case 'text':
      maxLen(out, `${p}.text`, w.text, LIMITS.textMax)
      break
    case 'clock':
      break
    case 'sensor':
      sensorId(out, `${p}.sensor`, w.sensor)
      range(out, `${p}.decimals`, w.decimals, LIMITS.decimalsMin, LIMITS.decimalsMax)
      maxLen(out, `${p}.prefix`, w.prefix, LIMITS.shortTextMax)
      maxLen(out, `${p}.suffix`, w.suffix, LIMITS.shortTextMax)
      range(out, `${p}.interval`, w.interval, LIMITS.intervalMin, LIMITS.intervalMax, false)
      range(out, `${p}.max_chars`, w.max_chars, LIMITS.maxCharsMin, LIMITS.maxCharsMax)
      break
    case 'bar':
      sensorId(out, `${p}.sensor`, w.sensor)
      range(out, `${p}.min`, w.min, -1e12, 1e12, false)
      range(out, `${p}.max`, w.max, -1e12, 1e12, false)
      if (w.max <= w.min) out.push({ path: `${p}.max`, message: 'El máximo tiene que ser mayor que el mínimo.' })
      color(out, `${p}.fill`, w.fill)
      color(out, `${p}.track`, w.track)
      range(out, `${p}.interval`, w.interval, LIMITS.intervalMin, LIMITS.intervalMax, false)
      break
    case 'image': {
      const err = checkPath(w.file, IMAGE_EXTENSIONS)
      if (err) out.push({ path: `${p}.file`, message: err })
      break
    }
  }
  return out
}

export function validateTheme(theme: Theme): Issue[] {
  const out: Issue[] = []
  const name = theme.name
  if (name.trim().length < 1) out.push({ path: 'name', message: 'El tema necesita un nombre.' })
  maxLen(out, 'name', name, LIMITS.nameMax)
  if (theme.author !== null) maxLen(out, 'author', theme.author, LIMITS.authorMax)
  color(out, 'background.color', theme.background.color)
  if (theme.background.image !== null) {
    const err = checkPath(theme.background.image, IMAGE_EXTENSIONS)
    if (err) out.push({ path: 'background.image', message: err })
  }
  if (theme.widgets.length > MAX_WIDGETS) {
    out.push({ path: 'widgets', message: `Como mucho ${MAX_WIDGETS} elementos por tema.` })
  }
  theme.widgets.forEach((w, i) => out.push(...validateWidget(w, i, theme)))
  return out
}

/** Índice del elemento al que apunta una ruta («widgets.3.box.x» → 3), o null. */
export function widgetIndexOf(path: string): number | null {
  const m = /^widgets\.(\d+)(\.|$)/.exec(path)
  return m ? Number(m[1]) : null
}

/** Campo dentro del elemento («widgets.3.box.x» → «box.x»). */
export function fieldOf(path: string): string {
  return path.replace(/^widgets\.\d+\.?/, '')
}
