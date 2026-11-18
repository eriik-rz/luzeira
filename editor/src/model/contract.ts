// Reglas del contrato con el servidor que no dependen del navegador (para poder
// probarlas con node:test). Ver docs/planning/fase-5-editor.md, «Decisiones y cambios
// al contrato (2026-11-17)».

/** Cabecera con el nombre sugerido de una subida (el servidor lo sanea siempre). */
export const FILENAME_HEADER = 'X-Visoria-Filename'

/**
 * Valor de la cabecera `X-Visoria-Filename`: codificado en URL, porque una cabecera
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
export function messageForStatus(status: number, serverMessage?: string | null): string {
  if (status === 403) return 'Visoria ha rechazado la petición. Cierra el editor y vuelve a abrirlo.'
  if (serverMessage) return serverMessage
  if (status === 404) return 'No se ha encontrado.'
  if (status === 409) return 'Este tema no se puede cambiar. Usa «Guardar como…» para hacer tu copia.'
  if (status === 413) return 'El fichero es demasiado grande.'
  return `Algo ha fallado (código ${status}).`
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

/** Respuesta de `window.visoriaEditor.flush()`, que la ventana lee al cerrar. */
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

/** Aviso tras «Aplicar», según el estado que da `GET /api/info` justo después. */
export function appliedMessage(
  title: string,
  info: { status: string; message: string } | null,
): { message: string; kind: 'ok' | 'info' | 'error' } {
  const saved = 'Guardado como tema de la pantalla.'
  if (!info) return { message: `${saved} No se ha podido comprobar cómo está la pantalla.`, kind: 'info' }
  switch (info.status) {
    case 'running':
      return { message: `Listo: la pantalla muestra «${title}».`, kind: 'ok' }
    case 'starting':
      return { message: `Listo: «${title}» se está poniendo en la pantalla.`, kind: 'ok' }
    case 'no_display':
      return { message: `${saved} Se verá cuando la conectes.`, kind: 'info' }
    case 'suspended':
      return { message: `${saved} Se verá cuando el equipo salga del reposo.`, kind: 'info' }
    case 'stopped':
      return { message: `${saved} Se verá cuando Visoria ponga la pantalla en marcha.`, kind: 'info' }
    case 'simulado':
      return { message: `Aplicado «${title}» (modo simulado: no hay pantalla de verdad).`, kind: 'info' }
    case 'theme_error':
    case 'error':
      return { message: `${saved} Pero la pantalla tiene un problema: ${info.message}`, kind: 'error' }
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

/** El tema por defecto de Visoria (`DEFAULT_THEME` de `src/visoria/config.py`; un test de
 * Python vigila que coincidan). Es el que se pone si se borra el que estaba en pantalla. */
export const DEFAULT_THEME = 'esencial'

/** Aviso tras mover un tema a la papelera. */
export function deletedMessage(wasOnScreen: boolean): string {
  const moved = 'Tema movido a la papelera de Visoria.'
  return wasOnScreen ? `${moved} En la pantalla se ha puesto «Esencial».` : moved
}

/** Por qué «Aplicar» pregunta antes (o null si se puede aplicar ya). */
export type ApplyBlock = 'pending' | 'invalid' | 'builtin-changed'

export function applyBlock(s: { builtin: boolean; dirty: boolean; invalid: boolean }): ApplyBlock | null {
  if (!s.dirty) return null
  if (s.builtin) return 'builtin-changed'
  return s.invalid ? 'invalid' : 'pending'
}

export const APPLY_BLOCK_TEXT: Record<ApplyBlock, { title: string; body: string }> = {
  pending: {
    title: 'Hay cambios sin guardar',
    body: 'Para ponerlo en la pantalla hay que guardar antes tus últimos cambios.',
  },
  invalid: {
    title: 'Hay errores sin corregir',
    body: 'Los cambios con errores no se pueden guardar. Corrige los campos marcados en rojo, o pon en la pantalla la última versión guardada.',
  },
  'builtin-changed': {
    title: 'Los temas incluidos no se pueden cambiar',
    body: 'Tus cambios solo se conservan en una copia tuya. Guárdala con «Guardar como…» y se aplicará, o pon en la pantalla el tema original.',
  },
}
