// Validación en el cliente: lo básico, para avisar al momento mientras se edita.
// La autoridad es el servidor (pydantic, modelo.py); aquí se repiten sus reglas con
// las mismas rutas que usan sus `details` («widgets.3.box.x») para pintarlas igual.

import {
  COLOR_RE,
  FONT_EXTENSIONS,
  IMAGE_EXTENSIONS,
  LANG_CODE_RE,
  LIMITS,
  MAX_WIDGETS,
  REL_PATH_RE,
  SENSOR_ID_RE,
  screenSize,
  type Theme,
  type Translations,
  type Widget,
} from './types.ts'
import { localNumber } from './format.ts'
import { t } from './i18n.ts'

export interface Issue {
  path: string // «name», «widgets.3.box.x», «» (general)
  message: string
}

// Nombres reservados de Windows (los mismos que comprueba temas/rutas.py).
const RESERVED = /^(con|prn|aux|nul|com[0-9]|lpt[0-9])$/i

function checkPath(path: string, allowed: readonly string[]): string | null {
  if (path.length < 1 || path.length > LIMITS.pathMax || !REL_PATH_RE.test(path)) {
    return t('modelo.validar.fichero')
  }
  if (path.split('/').some((part) => RESERVED.test(part.split('.')[0]))) {
    return t('modelo.validar.reservado')
  }
  const ext = path.slice(path.lastIndexOf('.') + 1).toLowerCase()
  if (!allowed.includes(ext)) return t('modelo.validar.extension', { lista: allowed.join(', ') })
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
    out.push({ path, message: integer ? t('modelo.validar.entero') : t('modelo.validar.numero') })
  } else if (value < min || value > max) {
    out.push({ path, message: t('modelo.validar.entre', { min: localNumber(min), max: localNumber(max) }) })
  }
}

function color(out: Issue[], path: string, value: string | null) {
  if (value !== null && !COLOR_RE.test(value)) {
    out.push({ path, message: t('modelo.validar.color') })
  }
}

function maxLen(out: Issue[], path: string, value: string, max: number) {
  if (value.length > max) out.push({ path, message: t('modelo.validar.largo', { max }) })
}

function sensorId(out: Issue[], path: string, value: string) {
  if (!SENSOR_ID_RE.test(value) || value.length > LIMITS.sensorIdMax) {
    out.push({ path, message: t('modelo.validar.sensor') })
  }
}

/** Traducciones de un texto (C-62): las mismas reglas que `TextTranslations` en modelo.py. */
function translations(out: Issue[], path: string, value: Translations | undefined, max: number) {
  if (value === undefined || value === null) return
  if (typeof value !== 'object' || Array.isArray(value)) {
    out.push({ path, message: t('modelo.validar.traducciones') })
    return
  }
  const entries = Object.entries(value)
  if (entries.length > LIMITS.translationsMax) {
    out.push({ path, message: t('modelo.validar.traducciones_muchas', { max: LIMITS.translationsMax }) })
  }
  for (const [code, text] of entries) {
    if (!LANG_CODE_RE.test(code)) {
      out.push({ path, message: t('modelo.validar.traduccion_codigo', { codigo: code }) })
    } else if (typeof text !== 'string') {
      out.push({ path, message: t('modelo.validar.traducciones') })
    } else if (text.length > max) {
      out.push({ path, message: t('modelo.validar.traduccion_larga', { codigo: code, max }) })
    }
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
    out.push({ path: `${p}.box.width`, message: t('modelo.validar.derecha', { ancho: width }) })
  }
  if (b.y + b.height > height) {
    out.push({ path: `${p}.box.height`, message: t('modelo.validar.abajo', { alto: height }) })
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
      translations(out, `${p}.text_i18n`, w.text_i18n, LIMITS.textMax)
      break
    case 'clock':
      break
    case 'sensor':
      sensorId(out, `${p}.sensor`, w.sensor)
      range(out, `${p}.decimals`, w.decimals, LIMITS.decimalsMin, LIMITS.decimalsMax)
      maxLen(out, `${p}.prefix`, w.prefix, LIMITS.shortTextMax)
      maxLen(out, `${p}.suffix`, w.suffix, LIMITS.shortTextMax)
      translations(out, `${p}.prefix_i18n`, w.prefix_i18n, LIMITS.shortTextMax)
      translations(out, `${p}.suffix_i18n`, w.suffix_i18n, LIMITS.shortTextMax)
      range(out, `${p}.interval`, w.interval, LIMITS.intervalMin, LIMITS.intervalMax, false)
      range(out, `${p}.max_chars`, w.max_chars, LIMITS.maxCharsMin, LIMITS.maxCharsMax)
      break
    case 'bar':
      sensorId(out, `${p}.sensor`, w.sensor)
      range(out, `${p}.min`, w.min, -1e12, 1e12, false)
      range(out, `${p}.max`, w.max, -1e12, 1e12, false)
      if (w.max <= w.min) out.push({ path: `${p}.max`, message: t('modelo.validar.max_min') })
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
  if (name.trim().length < 1) out.push({ path: 'name', message: t('modelo.validar.nombre') })
  maxLen(out, 'name', name, LIMITS.nameMax)
  if (theme.author !== null) maxLen(out, 'author', theme.author, LIMITS.authorMax)
  color(out, 'background.color', theme.background.color)
  if (theme.background.image !== null) {
    const err = checkPath(theme.background.image, IMAGE_EXTENSIONS)
    if (err) out.push({ path: 'background.image', message: err })
  }
  if (theme.widgets.length > MAX_WIDGETS) {
    out.push({ path: 'widgets', message: t('modelo.validar.elementos', { max: MAX_WIDGETS }) })
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
