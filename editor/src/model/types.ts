// Tipos del formato de temas «luzeira-theme/1».
//
// Reflejan exactamente src/luzeira/temas/modelo.py (la autoridad). Hay dos niveles:
//  - `*Json`: lo que puede venir en un theme.json (los campos con valor por defecto
//    en Python son opcionales aquí).
//  - Sin sufijo: la versión «normalizada» con la que trabaja el editor, con todos los
//    campos rellenos. Guardar la versión normalizada es válido para el servidor.

export const FORMAT = 'luzeira-theme/1'
export const MAX_WIDGETS = 64

export type Orientation = 'portrait' | 'reverse-portrait' | 'landscape' | 'reverse-landscape'
export type Align = 'left' | 'center' | 'right'
export type ClockFormat = 'HH:MM' | 'HH:MM:SS' | 'date-short' | 'date-long' | 'weekday'
export type BarDirection = 'horizontal' | 'vertical'
export type WidgetType = 'text' | 'clock' | 'sensor' | 'bar' | 'image'
/**
 * Traducciones de un texto del tema (C-62): `{"en": "NET", "de": "NETZ"}`. Solo datos; si
 * falta el idioma activo se pinta el texto normal (`localized` en format.ts).
 */
export type Translations = Record<string, string>

/** Rectángulo en píxeles de la pantalla real. */
export interface Box {
  x: number
  y: number
  width: number
  height: number
}

export interface StyleJson {
  font?: string | null
  size?: number
  bold?: boolean
  color?: string
  align?: Align
  background?: string | null
}

export interface Style {
  font: string | null
  size: number
  bold: boolean
  color: string
  align: Align
  background: string | null
}

interface WidgetBaseJson {
  box: Box
  style?: StyleJson
}

export interface TextWidgetJson extends WidgetBaseJson {
  type: 'text'
  text: string
  text_i18n?: Translations | null
}
export interface ClockWidgetJson extends WidgetBaseJson {
  type: 'clock'
  format?: ClockFormat
}
export interface SensorWidgetJson extends WidgetBaseJson {
  type: 'sensor'
  sensor: string
  decimals?: number
  prefix?: string
  suffix?: string
  prefix_i18n?: Translations | null
  suffix_i18n?: Translations | null
  show_unit?: boolean
  interval?: number
  max_chars?: number
}
export interface BarWidgetJson extends WidgetBaseJson {
  type: 'bar'
  sensor: string
  min?: number
  max?: number
  direction?: BarDirection
  fill?: string
  track?: string
  interval?: number
}
export interface ImageWidgetJson extends WidgetBaseJson {
  type: 'image'
  file: string
}

export type WidgetJson =
  | TextWidgetJson
  | ClockWidgetJson
  | SensorWidgetJson
  | BarWidgetJson
  | ImageWidgetJson

export interface BackgroundJson {
  color?: string
  image?: string | null
}

export interface ThemeJson {
  format: typeof FORMAT
  name: string
  author?: string | null
  orientation: Orientation
  background?: BackgroundJson
  widgets?: WidgetJson[]
}

// --- Versión normalizada (todo relleno) ---------------------------------------------

interface WidgetBase {
  box: Box
  style: Style
}
export interface TextWidget extends WidgetBase {
  type: 'text'
  text: string
  /** Solo si el tema las trae: se conservan tal cual al guardar (no se rellenan). */
  text_i18n?: Translations
}
export interface ClockWidget extends WidgetBase {
  type: 'clock'
  format: ClockFormat
}
export interface SensorWidget extends WidgetBase {
  type: 'sensor'
  sensor: string
  decimals: number
  prefix: string
  suffix: string
  prefix_i18n?: Translations
  suffix_i18n?: Translations
  show_unit: boolean
  interval: number
  max_chars: number
}
export interface BarWidget extends WidgetBase {
  type: 'bar'
  sensor: string
  min: number
  max: number
  direction: BarDirection
  fill: string
  track: string
  interval: number
}
export interface ImageWidget extends WidgetBase {
  type: 'image'
  file: string
}

export type Widget = TextWidget | ClockWidget | SensorWidget | BarWidget | ImageWidget

export interface Background {
  color: string
  image: string | null
}

export interface Theme {
  format: typeof FORMAT
  name: string
  author: string | null
  orientation: Orientation
  background: Background
  widgets: Widget[]
}

// --- Límites (los mismos números que modelo.py) -------------------------------------

export const LIMITS = {
  nameMax: 60,
  authorMax: 60,
  textMax: 200,
  shortTextMax: 20,
  boxCoordMax: 2048,
  sizeMin: 6,
  sizeMax: 300,
  decimalsMin: 0,
  decimalsMax: 3,
  intervalMin: 0.5,
  intervalMax: 3600,
  maxCharsMin: 1,
  maxCharsMax: 200,
  pathMax: 120,
  sensorIdMax: 60,
  /** Traducciones de un texto: como mucho 20 idiomas, con código de dos letras. */
  translationsMax: 20,
} as const

export const LANG_CODE_RE = /^[a-z]{2}$/

export const COLOR_RE = /^#[0-9a-fA-F]{6}$/
export const SENSOR_ID_RE = /^[a-z0-9_]+(\.[a-z0-9_]+)+$/
export const REL_PATH_RE = /^([A-Za-z0-9_-]+\/)*[A-Za-z0-9_-]+\.[A-Za-z0-9]{2,5}$/
export const IMAGE_EXTENSIONS = ['png', 'jpg', 'jpeg', 'gif'] as const
export const FONT_EXTENSIONS = ['ttf', 'otf'] as const

/** Tamaño de la pantalla (ancho, alto) según la orientación. */
export const ORIENTATION_SIZES: Record<Orientation, { width: number; height: number }> = {
  portrait: { width: 320, height: 480 },
  'reverse-portrait': { width: 320, height: 480 },
  landscape: { width: 480, height: 320 },
  'reverse-landscape': { width: 480, height: 320 },
}

export interface Size {
  width: number
  height: number
}

export function screenSize(theme: Pick<Theme, 'orientation'>): Size {
  return ORIENTATION_SIZES[theme.orientation]
}
