// Idiomas del editor (C-62). Lo mismo que `luzeira/idioma.py`, pero para la interfaz:
// el código pide un texto por su clave (`t('ajustes.idioma.titulo')`) y cada idioma tiene
// su catálogo en `src/i18n/<código>/<zona>.json`.
//
// Este módulo es puro (sin React ni Vite) para poder usarlo desde `model/*` y probarlo con
// `node --test`. Los catálogos los registra `src/i18n/catalogos.ts` al arrancar (y los
// tests, leyéndolos del disco).

export type Catalog = Record<string, string>

/** Los que se mantienen completos siempre; si falta un texto se usa el inglés y luego el español. */
export const COMPLETE = ['es', 'en'] as const
export const FALLBACK = 'en'
/** Nombre de cada idioma en su propio idioma (el mismo que en `idioma.py`). */
export const NATIVE_NAMES: Record<string, string> = {
  es: 'Español', // i18n: no
  en: 'English',
  de: 'Deutsch',
  fr: 'Français', // i18n: no
  it: 'Italiano',
  pt: 'Português', // i18n: no
  pl: 'Polski',
  nl: 'Nederlands',
  ru: 'Русский',
}

let catalogs: Record<string, Catalog> = {}
let active = 'es'

export function registerCatalogs(c: Record<string, Catalog>): void {
  catalogs = c
}

export function availableLanguages(): string[] {
  return Object.keys(NATIVE_NAMES).filter((code) => code in catalogs)
}

/** El idioma que se usa: el pedido si lo tenemos; si no, el del navegador/sistema; si no, inglés. */
export function resolveLanguage(code: string | null | undefined, system?: string | null): string {
  const langs = availableLanguages()
  if (code && langs.includes(code)) return code
  const sys = (system ?? '').slice(0, 2).toLowerCase()
  return langs.includes(sys) ? sys : FALLBACK
}

export function setLanguage(code: string | null | undefined, system?: string | null): string {
  active = resolveLanguage(code, system)
  return active
}

export function currentLanguage(): string {
  return active
}

/** El texto de `key` en el idioma activo con sus `{variables}` puestas. Si no está en ninguno, la clave. */
export function t(key: string, vars?: Record<string, string | number>): string {
  const text = catalogs[active]?.[key] ?? catalogs[FALLBACK]?.[key] ?? catalogs.es?.[key]
  if (text === undefined) return key
  if (!vars) return text
  return text.replace(/\{(\w+)\}/g, (whole, name: string) => (name in vars ? String(vars[name]) : whole))
}
