// Lo que los paneles necesitan del editor además del estado del tema.

import type { EditorApi, SensorInfo } from '../api.ts'
import { localNumber } from '../model/format.ts'
import { t } from '../model/i18n.ts'

export type ResourceKind = 'image' | 'font'

export interface Services {
  api: EditorApi
  themeName: string
  builtin: boolean
  files: string[]
  sensors: SensorInfo[]
  /** Abre el selector de ficheros y sube lo elegido. null si se cancela o falla. */
  upload: (kind: ResourceKind) => Promise<{ path: string; file: File } | null>
}

const CATEGORIES = ['cpu', 'gpu', 'memory', 'network', 'disk', 'system']

/** Nombre de una categoría de sensores en el idioma activo (la desconocida, tal cual). */
export function categoryLabel(category: string): string {
  return CATEGORIES.includes(category) ? t(`recursos.categoria.${category}`) : category
}

/** Valor actual de un sensor tal y como lo da el servidor, para enseñarlo al lado. */
export function liveText(s: SensorInfo | undefined): string | null {
  if (!s) return null
  if (s.text !== null) return s.text
  if (s.value === null) return null
  const v = Number.isInteger(s.value) ? String(s.value) : localNumber(s.value.toFixed(1))
  return s.unit ? `${v} ${s.unit}` : v
}
