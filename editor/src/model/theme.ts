// Normalización, valores por defecto y textos de los tipos de elemento.
// Funciones puras: se prueban con `node --test` (tests/*.test.ts).

import {
  FORMAT,
  screenSize,
  type Background,
  type Box,
  type ClockFormat,
  type Orientation,
  type Size,
  type Style,
  type StyleJson,
  type Theme,
  type ThemeJson,
  type Translations,
  type Widget,
  type WidgetJson,
  type WidgetType,
} from './types.ts'
import { clampBox } from './geometry.ts'
import { t } from './i18n.ts'

/** Los mismos valores por defecto que `Style()` en modelo.py. */
export const DEFAULT_STYLE: Style = {
  font: null,
  size: 20,
  bold: false,
  color: '#ffffff',
  align: 'center',
  background: null,
}

export function normalizeStyle(s: StyleJson | undefined): Style {
  return {
    font: s?.font ?? null,
    size: s?.size ?? DEFAULT_STYLE.size,
    bold: s?.bold ?? DEFAULT_STYLE.bold,
    color: s?.color ?? DEFAULT_STYLE.color,
    align: s?.align ?? DEFAULT_STYLE.align,
    background: s?.background ?? null,
  }
}

/**
 * Las traducciones de un texto del tema (C-62), copiadas tal cual si el tema las trae. Sin
 * ellas el campo ni aparece: así un tema sin traducciones se guarda igual que siempre y
 * «Guardar como…» de un tema incluido no las pierde.
 */
function translations<K extends string>(key: K, value: Translations | null | undefined): Partial<Record<K, Translations>> {
  if (!value || typeof value !== 'object') return {}
  return { [key]: { ...value } } as Partial<Record<K, Translations>>
}

/** Rellena los campos que el JSON puede omitir con los valores por defecto de Python. */
export function normalizeWidget(w: WidgetJson): Widget {
  const box = { ...w.box }
  const style = normalizeStyle(w.style)
  switch (w.type) {
    case 'text':
      return { type: 'text', box, style, text: w.text, ...translations('text_i18n', w.text_i18n) }
    case 'clock':
      return { type: 'clock', box, style, format: w.format ?? 'HH:MM' }
    case 'sensor':
      return {
        type: 'sensor',
        box,
        style,
        sensor: w.sensor,
        decimals: w.decimals ?? 0,
        prefix: w.prefix ?? '',
        suffix: w.suffix ?? '',
        ...translations('prefix_i18n', w.prefix_i18n),
        ...translations('suffix_i18n', w.suffix_i18n),
        show_unit: w.show_unit ?? true,
        interval: w.interval ?? 1,
        max_chars: w.max_chars ?? 40,
      }
    case 'bar':
      return {
        type: 'bar',
        box,
        style,
        sensor: w.sensor,
        min: w.min ?? 0,
        max: w.max ?? 100,
        direction: w.direction ?? 'horizontal',
        fill: w.fill ?? '#ff8c00',
        track: w.track ?? '#2a2f3a',
        interval: w.interval ?? 1,
      }
    case 'image':
      return { type: 'image', box, style, file: w.file }
  }
}

export function normalizeTheme(t: ThemeJson): Theme {
  const background: Background = {
    color: t.background?.color ?? '#000000',
    image: t.background?.image ?? null,
  }
  return {
    format: FORMAT,
    name: t.name,
    author: t.author ?? null,
    orientation: t.orientation,
    background,
    widgets: (t.widgets ?? []).map(normalizeWidget),
  }
}

// --- Textos para el usuario ----------------------------------------------------------
// Funciones (no constantes): el texto se busca en el idioma activo cada vez que se pinta.

export function widgetTypeInfo(type: WidgetType): { label: string; help: string } {
  return { label: t(`modelo.tipo.${type}`), help: t(`modelo.tipo.${type}.ayuda`) }
}

export function orientationInfo(o: Orientation): { label: string; help: string } {
  return { label: t(`modelo.orientacion.${o}`), help: t(`modelo.orientacion.${o}.ayuda`) }
}

export function clockFormatLabel(f: ClockFormat): string {
  return t(`modelo.reloj.${f}`)
}

// --- Elementos nuevos ----------------------------------------------------------------

/** Tamaño inicial de cada tipo, pensado para la pantalla horizontal de 480 × 320. */
const NEW_SIZES: Record<WidgetType, Size> = {
  text: { width: 160, height: 32 },
  clock: { width: 160, height: 48 },
  sensor: { width: 160, height: 40 },
  bar: { width: 200, height: 12 },
  image: { width: 96, height: 96 },
}

export interface NewWidgetOptions {
  sensor?: string // sensor por defecto para Sensor y Barra
  file?: string // fichero para Imagen
  imageSize?: Size // tamaño real de la imagen subida
  /** Punto de la pantalla donde centrarlo (al soltar una imagen sobre el lienzo). */
  center?: { x: number; y: number }
}

/**
 * Coloca un elemento nuevo en el centro de la pantalla. Si ya hay otro exactamente en
 * ese sitio, lo desplaza en diagonal (8 px) para que se vea que hay dos. Nunca se sale.
 */
export function placeNewBox(size: Size, screen: Size, existing: Box[]): Box {
  const width = Math.min(size.width, screen.width)
  const height = Math.min(size.height, screen.height)
  let x = Math.round((screen.width - width) / 2 / 4) * 4
  let y = Math.round((screen.height - height) / 2 / 4) * 4
  for (let i = 0; i < 32; i++) {
    if (!existing.some((b) => b.x === x && b.y === y)) break
    x += 8
    y += 8
    if (x + width > screen.width || y + height > screen.height) {
      x = 0
      y = 0
    }
  }
  return clampBox({ x, y, width, height }, screen)
}

/** Ajusta el tamaño de una imagen para que ocupe como mucho la mitad de la pantalla. */
export function fitImageSize(image: Size, screen: Size): Size {
  const maxW = Math.floor(screen.width / 2)
  const maxH = Math.floor(screen.height / 2)
  const scale = Math.min(1, maxW / image.width, maxH / image.height)
  return {
    width: Math.max(1, Math.round(image.width * scale)),
    height: Math.max(1, Math.round(image.height * scale)),
  }
}

export function createWidget(
  type: WidgetType,
  theme: Theme,
  options: NewWidgetOptions = {},
): Widget {
  const screen = screenSize(theme)
  const existing = theme.widgets.map((w) => w.box)
  const size = type === 'image' && options.imageSize
    ? fitImageSize(options.imageSize, screen)
    : NEW_SIZES[type]
  const box = options.center
    ? clampBox(
        {
          x: Math.round(options.center.x - size.width / 2),
          y: Math.round(options.center.y - size.height / 2),
          width: Math.min(size.width, screen.width),
          height: Math.min(size.height, screen.height),
        },
        screen,
      )
    : placeNewBox(size, screen, existing)
  const style = { ...DEFAULT_STYLE }
  const sensor = options.sensor ?? 'cpu.usage'
  switch (type) {
    case 'text':
      return { type, box, style: { ...style, size: 20 }, text: t('modelo.nuevo_texto') }
    case 'clock':
      return { type, box, style: { ...style, size: 36, bold: true }, format: 'HH:MM' }
    case 'sensor':
      return {
        type,
        box,
        style: { ...style, size: 28, bold: true },
        sensor,
        decimals: 0,
        prefix: '',
        suffix: '',
        show_unit: true,
        interval: 1,
        max_chars: 40,
      }
    case 'bar':
      return {
        type,
        box,
        style,
        sensor,
        min: 0,
        max: 100,
        direction: 'horizontal',
        fill: '#ff8c00',
        track: '#2a2f3a',
        interval: 1,
      }
    case 'image':
      return { type, box, style, file: options.file ?? 'imagen.png' }
  }
}

/** Copia de un elemento desplazada 8 px (sin salirse de la pantalla). */
export function duplicateWidget(w: Widget, screen: Size): Widget {
  const copy = structuredCloneWidget(w)
  copy.box = clampBox({ ...w.box, x: w.box.x + 8, y: w.box.y + 8 }, screen)
  return copy
}

function structuredCloneWidget(w: Widget): Widget {
  return JSON.parse(JSON.stringify(w)) as Widget
}

/** Nombre corto del fichero (sin carpetas) para mostrarlo. */
export function baseName(path: string): string {
  const i = path.lastIndexOf('/')
  return i >= 0 ? path.slice(i + 1) : path
}

export function isImagePath(path: string): boolean {
  return /\.(png|jpe?g|gif)$/i.test(path)
}

export function isFontPath(path: string): boolean {
  return /\.(ttf|otf)$/i.test(path)
}
