// Textos de reloj y sensores, igual que render/motor.py (format_clock y format_reading).
// El editor los usa para los ejemplos de los selectores y para la vista previa simulada.

import type { ClockFormat, SensorWidget } from './types.ts'

const WEEKDAYS = ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado']
const MONTHS = [
  'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
  'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
]
export const NO_VALUE = '—'

const two = (n: number) => String(n).padStart(2, '0')

export function formatClock(fmt: ClockFormat, d: Date): string {
  switch (fmt) {
    case 'HH:MM':
      return `${two(d.getHours())}:${two(d.getMinutes())}`
    case 'HH:MM:SS':
      return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`
    case 'date-short':
      return `${two(d.getDate())}/${two(d.getMonth() + 1)}/${d.getFullYear()}`
    case 'weekday':
      return WEEKDAYS[d.getDay()]
    case 'date-long':
      return `${WEEKDAYS[d.getDay()]}, ${d.getDate()} de ${MONTHS[d.getMonth()]}`
  }
}

export interface ReadingLike {
  value: number | null
  unit: string
  text: string | null
}

type ReadingFields = Pick<SensorWidget, 'decimals' | 'prefix' | 'suffix' | 'show_unit' | 'max_chars'>

export function formatReading(w: ReadingFields, r: ReadingLike | undefined): string {
  if (r && r.text !== null) {
    const text = r.text.length > w.max_chars ? r.text.slice(0, w.max_chars - 1) + '…' : r.text
    return `${w.prefix}${text}${w.suffix}`
  }
  if (!r || r.value === null) return `${w.prefix}${NO_VALUE}`
  const number = r.value.toFixed(w.decimals).replace('.', ',')
  const unit = w.show_unit && r.unit ? ` ${r.unit}` : ''
  return `${w.prefix}${number}${unit}${w.suffix}`
}
