// Reglas del contrato con el servidor que no dependen del navegador (para poder
// probarlas con node:test). Ver docs/planning/fase-5-editor.md, «Decisiones y cambios
// al contrato (2026-11-17)».

import { t } from './i18n.ts'

/** Cabecera con el nombre sugerido de una subida (el servidor lo sanea siempre). */
export const FILENAME_HEADER = 'X-Luzeira-Filename'

/**
 * Valor de la cabecera `X-Luzeira-Filename`: codificado en URL, porque una cabecera
 * HTTP solo admite ASCII y el nombre puede traer tildes, eñes o espacios.
 */
export function encodeFilenameHeader(name: string): string {
  return encodeURIComponent(name)
}

/**
 * Mensaje para el usuario según el código HTTP y lo que diga el servidor.
 * - 403 es SOLO seguridad (token, Host, Origin) y llega sin detalles: siempre el texto
 *   genérico, aunque el cuerpo trajera algo.
 * - 409 es «no se puede cambiar» (tema incluido de solo lectura, tema en pantalla…):
 *   manda el mensaje del servidor.
 */
/** Cabecera de la vista previa con las letras que faltan (ver servidor.py). */
export const MISSING_GLYPHS_HEADER = 'X-Luzeira-Missing-Glyphs'

/** Lee esa cabecera; si falta o no es válida, ninguna (nunca rompe la vista previa). */
export function parseMissingGlyphs(header: string | null): { index: number; chars: string }[] {
  if (!header) return []
  try {
    const data: unknown = JSON.parse(header)
    if (!Array.isArray(data)) return []
    return data.filter(
      (x): x is { index: number; chars: string } =>
        typeof x === 'object' && x !== null && Number.isInteger(x.index) && typeof x.chars === 'string' && x.chars.length > 0,
    )
  } catch {
    return []
  }
}

/** Texto del aviso: «★» en «Texto: Hola» y «€» en «Sensor: …». */
export function missingGlyphsMessage(items: { index: number; chars: string }[], labels: string[]): string {
  return items
    .map((m) =>
      t('modelo.letras.en', {
        letras: [...m.chars].map((c) => t('modelo.letras.letra', { c })).join(' '),
        elemento: labels[m.index] ?? t('modelo.letras.un_elemento'),
      }),
    )
    .join('; ')
}

export function messageForStatus(status: number, serverMessage?: string | null): string {
  if (status === 403) return t('modelo.http.403')
  if (serverMessage) return serverMessage // ya viene traducido de Luzeira
  if (status === 404) return t('modelo.http.404')
  if (status === 409) return t('modelo.http.409')
  if (status === 413) return t('modelo.http.413')
  return t('modelo.http.otro', { codigo: status })
}

/**
 * Tema con el que se abre el editor: el que está en la pantalla (`info.active_theme`) si
 * existe en la lista; si no, el primero de «Mis temas»; si no, el primero incluido.
 */
export function pickInitialTheme<T extends { name: string; builtin: boolean }>(
  themes: T[],
  active?: string | null,
): T | undefined {
  return (
    (active ? themes.find((t) => t.name === active) : undefined) ??
    themes.find((t) => !t.builtin) ??
    themes[0]
  )
}

// --- Remate 2: contrato ventana ↔ interfaz al cerrar -----------------------------------

/** Lo que queda sin guardar al cerrar. Ver docs/planning/fase-5-editor.md, «Remate 2». */
export type Pending = 'none' | 'invalid' | 'builtin' | 'error'

/** Respuesta de `window.luzeiraEditor.flush()`, que la ventana lee al cerrar. */
export interface FlushResult {
  saved: boolean
  pending: Pending
}

/** Resultado de intentar guardar una vez. */
export type SaveOutcome = 'ok' | 'invalid' | 'error'

/**
 * Qué responder a la ventana tras guardar lo pendiente.
 * - `dirty`: lo que se ve sigue siendo distinto de lo último guardado.
 * - `builtin`: el tema es incluido (de solo lectura): sus cambios no se pueden guardar.
 * - `invalid`: hay errores en rojo (del navegador o del servidor).
 * - `lastSave`: cómo acabó el último intento de guardado, si lo hubo.
 */
export function flushOutcome(s: { dirty: boolean; builtin: boolean; invalid: boolean; lastSave?: SaveOutcome }): FlushResult {
  if (!s.dirty) return { saved: true, pending: 'none' }
  if (s.builtin) return { saved: false, pending: 'builtin' }
  if (s.invalid || s.lastSave === 'invalid') return { saved: false, pending: 'invalid' }
  return { saved: false, pending: 'error' }
}

/**
 * ¿Es la respuesta «vista previa superada»? El servidor la manda cuando una petición
 * vieja se queda sin pintar porque ya llegó otra más nueva: no es un error y no se
 * enseña nada (204, o 409 con `{"superseded": true}`).
 */
export function isSupersededPreview(status: number, body?: unknown): boolean {
  if (status === 204) return true
  return status === 409 && typeof body === 'object' && body !== null && (body as { superseded?: unknown }).superseded === true
}

/** Cómo se enseña en la barra el estado de la pantalla (`GET /api/info`).
 *  - `alert`: un problema que el usuario tiene que ver ya (late para llamar la atención y
 *    nunca se esconde por falta de sitio; el texto largo va en el `title`).
 *  - `note`: lo demás, discreto y recortable. */
export interface DisplayPill {
  kind: 'alert' | 'error' | 'note'
  text: string
  title: string
}

export function displayPill(info: { status: string; message: string } | null): DisplayPill | null {
  if (!info || !info.message) return null
  switch (info.status) {
    case 'no_display':
      return { kind: 'alert', text: t('modelo.pastilla.sin_pantalla'), title: info.message }
    case 'theme_error':
      return { kind: 'error', text: t('modelo.pastilla.error_tema'), title: info.message }
    case 'error':
      return { kind: 'error', text: t('modelo.pastilla.error_pantalla'), title: info.message }
    case 'simulado':
      return null // ya sale «Modo simulado»
    default:
      return { kind: 'note', text: info.message, title: info.message }
  }
}

/** Aviso tras «Aplicar», según el estado que da `GET /api/info` justo después. */
export function appliedMessage(
  title: string,
  info: { status: string; message: string } | null,
): { message: string; kind: 'ok' | 'info' | 'error' } {
  const saved = t('modelo.aplicado.guardado')
  const then = (key: string, vars?: Record<string, string>) => `${saved} ${t(key, vars)}`
  if (!info) return { message: then('modelo.aplicado.sin_comprobar'), kind: 'info' }
  switch (info.status) {
    case 'running':
      return { message: t('modelo.aplicado.running', { tema: title }), kind: 'ok' }
    case 'starting':
      return { message: t('modelo.aplicado.starting', { tema: title }), kind: 'ok' }
    case 'no_display':
      return { message: then('modelo.aplicado.no_display'), kind: 'info' }
    case 'suspended':
      return { message: then('modelo.aplicado.suspended'), kind: 'info' }
    case 'stopped':
      return { message: then('modelo.aplicado.stopped'), kind: 'info' }
    case 'simulado':
      return { message: t('modelo.aplicado.simulado', { tema: title }), kind: 'info' }
    case 'theme_error':
    case 'error':
      return { message: then('modelo.aplicado.problema', { mensaje: info.message }), kind: 'error' }
    default:
      return { message: info.message ? `${saved} ${info.message}` : saved, kind: 'info' }
  }
}

/**
 * ¿Se puede soltar este fichero sobre el lienzo para crear una Imagen? Manda la
 * extensión del nombre, que es lo que mira el servidor al subirlo.
 */
export function isDroppableImage(name: string): boolean {
  return /\.(png|jpe?g|gif)$/i.test(name)
}

/** El tema por defecto de Luzeira (`DEFAULT_THEME` de `src/luzeira/config.py`; un test de
 * Python vigila que coincidan). Es el que se pone si se borra el que estaba en pantalla. */
export const DEFAULT_THEME = 'esencial'

/** Aviso tras mover un tema a la papelera. */
export function deletedMessage(wasOnScreen: boolean): string {
  const moved = t('modelo.borrado')
  return wasOnScreen ? `${moved} ${t('modelo.borrado.en_pantalla')}` : moved
}

/** Por qué «Aplicar» pregunta antes (o null si se puede aplicar ya). */
export type ApplyBlock = 'pending' | 'invalid' | 'builtin-changed'

export function applyBlock(s: { builtin: boolean; dirty: boolean; invalid: boolean }): ApplyBlock | null {
  if (!s.dirty) return null
  if (s.builtin) return 'builtin-changed'
  return s.invalid ? 'invalid' : 'pending'
}

/** Título y explicación de la pregunta de «Aplicar» (en el idioma activo). */
export function applyBlockText(why: ApplyBlock): { title: string; body: string } {
  return { title: t(`modelo.aplicar.${why}`), body: t(`modelo.aplicar.${why}.texto`) }
}
