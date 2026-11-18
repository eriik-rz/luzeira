// Lógica pura de la ventana «Ajustes» (sin React, para poder probarla con node --test).

/** Lo mínimo que hace falta de una opción de «Modelo y puerto» (ver `DisplayOption`). */
export interface DisplayOptionLike {
  id: string | null
  current: boolean
  warning: string | null
}

/** Valor del selector «Modelo y puerto». */
export const AUTO = 'auto'
/** Elegida a mano pero fuera de la lista cerrada (modo avanzado o un puerto que ya no está). */
export const OTHER = 'otra'

export function displaySelectValue(s: { display_auto: boolean; choices: DisplayOptionLike[] }): string {
  if (s.display_auto) return AUTO
  return s.choices.find((o) => o.current && o.id !== null)?.id ?? OTHER
}

/** ¿Hay que aceptar un aviso antes de probar esta opción? (beta o modo avanzado) */
export function needsConfirmation(option: DisplayOptionLike): boolean {
  return option.warning !== null
}

/** Busca la opción elegida en las dos listas; `null` si no se puede elegir. */
export function findOption<T extends DisplayOptionLike>(id: string, lists: T[][]): T | null {
  for (const list of lists) {
    const found = list.find((o) => o.id === id)
    if (found) return found
  }
  return null
}
