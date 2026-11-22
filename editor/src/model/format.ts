// Textos de reloj y sensores, igual que render/motor.py (format_clock y format_reading).
// El editor los usa para los ejemplos de los selectores y para la vista previa simulada.
//
// Días, meses, el formato de las fechas y el separador decimal salen del catálogo
// `pantalla` (C-62), el mismo que usa Luzeira para la pantalla: así la vista previa del
// editor y la pantalla real dicen lo mismo en cada idioma.

import { currentLanguage, t } from './i18n.ts'
import type { ClockFormat, SensorWidget, Translations } from './types.ts'

export const NO_VALUE = '—'

const two = (n: number) => String(n).padStart(2, '0')

/** Separador decimal del idioma activo («,» en español, «.» en inglés). */
export function decimalSeparator(): string {
  const sep = t('pantalla.decimal')
  return sep === 'pantalla.decimal' ? ',' : sep // sin catálogo, la coma de siempre
}

/** Un número con el separador decimal del idioma activo («14,1» / «14.1»). */
export function localNumber(n: number | string): string {
  return String(n).replace('.', decimalSeparator())
}

/** El texto del tema en el idioma activo si lo trae traducido; si no, el de siempre. */
export function localized(text: string, translations: Translations | null | undefined, lang = currentLanguage()): string {
  return translations?.[lang] ?? text
}

// JS: getDay() da 0 = domingo; el catálogo empieza en lunes (como weekday() de Python).
const weekday = (d: Date) => t(`pantalla.dia.${(d.getDay() + 6) % 7}`)

export function formatClock(fmt: ClockFormat, d: Date): string {
  switch (fmt) {
    case 'HH:MM':
      return `${two(d.getHours())}:${two(d.getMinutes())}`
    case 'HH:MM:SS':
      return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`
    case 'date-short':
      return t('pantalla.fecha_corta', { dd: two(d.getDate()), mm: two(d.getMonth() + 1), aaaa: d.getFullYear() })
    case 'weekday':
      return weekday(d)
    case 'date-long':
      return t('pantalla.fecha_larga', { dia: weekday(d), d: d.getDate(), mes: t(`pantalla.mes.${d.getMonth() + 1}`) })
  }
}

export interface ReadingLike {
  value: number | null
  unit: string
  text: string | null
}

type ReadingFields = Pick<SensorWidget, 'decimals' | 'prefix' | 'suffix' | 'show_unit' | 'max_chars'> &
  Partial<Pick<SensorWidget, 'prefix_i18n' | 'suffix_i18n'>>

export function formatReading(w: ReadingFields, r: ReadingLike | undefined): string {
  const prefix = localized(w.prefix, w.prefix_i18n)
  const suffix = localized(w.suffix, w.suffix_i18n)
  if (r && r.text !== null) {
    const text = r.text.length > w.max_chars ? r.text.slice(0, w.max_chars - 1) + '…' : r.text
    return `${prefix}${text}${suffix}`
  }
  if (!r || r.value === null) return `${prefix}${NO_VALUE}`
  const number = r.value.toFixed(w.decimals).replace('.', decimalSeparator())
  const unit = w.show_unit && r.unit ? ` ${r.unit}` : ''
  return `${prefix}${number}${unit}${suffix}`
}
