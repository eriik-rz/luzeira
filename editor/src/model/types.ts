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
  /** Tamaño de la pantalla para la que está hecho (opcional; sin él, `ORIENTATION_SIZES`). */
  canvas?: Size | null
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
  /**
   * Solo si no es el de siempre (320 × 480 / 480 × 320): así un tema sin lienzo se guarda
   * igual que antes y lo siguen leyendo las versiones viejas de Luzeira.
   */
  canvas?: Size
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

/** Tamaño de un tema sin lienzo (ancho, alto) según la orientación. */
export const ORIENTATION_SIZES: Record<Orientation, { width: number; height: number }> = {
  portrait: { width: 320, height: 480 },
  'reverse-portrait': { width: 320, height: 480 },
  landscape: { width: 480, height: 320 },
  'reverse-landscape': { width: 480, height: 320 },
}

/**
 * Lienzos que puede pedir un tema, en vertical (`CANVAS_SIZES_PORTRAIT` en modelo.py, que
 * cuenta de qué pantalla sale cada uno; un test de Python compara las dos tablas). En
 * horizontal, los mismos girados.
 */
export const CANVAS_SIZES_PORTRAIT: readonly (readonly [number, number])[] = [
  [320, 480],
  [480, 480],
  [480, 800],
  [600, 1024],
  [480, 1920],
]

export interface Size {
  width: number
  height: number
}

export function isLandscape(o: Orientation): boolean {
  return o === 'landscape' || o === 'reverse-landscape'
}

/** Los lienzos que van con una orientación (vertical: ancho ≤ alto), del más pequeño al mayor. */
export function canvasSizesFor(o: Orientation): Size[] {
  return CANVAS_SIZES_PORTRAIT.map(([w, h]) => (isLandscape(o) ? { width: h, height: w } : { width: w, height: h }))
}

export function isAllowedCanvas(size: Size, o: Orientation): boolean {
  return canvasSizesFor(o).some((c) => c.width === size.width && c.height === size.height)
}

export function screenSize(theme: Pick<Theme, 'orientation'> & { canvas?: Size | null }): Size {
  return theme.canvas ? { width: theme.canvas.width, height: theme.canvas.height } : ORIENTATION_SIZES[theme.orientation]
}
