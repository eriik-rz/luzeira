// Lo que los paneles necesitan del editor además del estado del tema.

import type { EditorApi, SensorInfo } from '../api.ts'

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

export const CATEGORY_LABELS: Record<string, string> = {
  cpu: 'Procesador',
  gpu: 'Tarjeta gráfica',
  memory: 'Memoria',
  network: 'Red',
  disk: 'Discos',
  system: 'Sistema',
}

/** Valor actual de un sensor tal y como lo da el servidor, para enseñarlo al lado. */
export function liveText(s: SensorInfo | undefined): string | null {
  if (!s) return null
  if (s.text !== null) return s.text
  if (s.value === null) return null
  const v = Number.isInteger(s.value) ? String(s.value) : s.value.toFixed(1).replace('.', ',')
  return s.unit ? `${v} ${s.unit}` : v
}
