// Geometría del lienzo: mover y redimensionar recuadros con imán y sin salirse de la
// pantalla. Todo en píxeles de la pantalla real (el zoom lo aplica el componente).

import type { Box, Size } from './types.ts'

export const GRID = 4 // paso del imán, en píxeles de la pantalla
export const MIN_SIZE = 4 // ancho/alto mínimo al redimensionar con el ratón

export type Handle = 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw'
export const HANDLES: Handle[] = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w']

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))
export const snapValue = (v: number, step = GRID) => Math.round(v / step) * step

/** Mete un recuadro dentro de la pantalla: primero recorta el tamaño, luego lo desplaza. */
export function clampBox(box: Box, screen: Size): Box {
  const width = clamp(Math.round(box.width), 1, screen.width)
  const height = clamp(Math.round(box.height), 1, screen.height)
  return {
    x: clamp(Math.round(box.x), 0, screen.width - width),
    y: clamp(Math.round(box.y), 0, screen.height - height),
    width,
    height,
  }
}

/** ¿Cabe entero en la pantalla? (la misma regla que `_widgets_inside_screen` en Python). */
export function boxInside(box: Box, screen: Size): boolean {
  return (
    box.x >= 0 &&
    box.y >= 0 &&
    box.width >= 1 &&
    box.height >= 1 &&
    box.x + box.width <= screen.width &&
    box.y + box.height <= screen.height
  )
}

/**
 * Mueve `start` (dx, dy) píxeles. Con imán, la esquina superior izquierda cae en
 * múltiplos de 4; el tamaño no cambia nunca al mover.
 */
export function moveBox(start: Box, dx: number, dy: number, screen: Size, snap: boolean): Box {
  let x = start.x + dx
  let y = start.y + dy
  if (snap) {
    x = snapValue(x)
    y = snapValue(y)
  }
  return {
    ...start,
    x: clamp(Math.round(x), 0, screen.width - start.width),
    y: clamp(Math.round(y), 0, screen.height - start.height),
  }
}

/**
 * Redimensiona arrastrando un tirador. Solo se mueven los bordes que toca el tirador;
 * el borde opuesto queda fijo. Con imán, el borde que se mueve cae en múltiplos de 4.
 * El borde nunca pasa del contrario (tamaño mínimo `MIN_SIZE`) ni sale de la pantalla.
 */
export function resizeBox(
  start: Box,
  handle: Handle,
  dx: number,
  dy: number,
  screen: Size,
  snap: boolean,
): Box {
  let left = start.x
  let top = start.y
  let right = start.x + start.width
  let bottom = start.y + start.height
  const s = (v: number) => (snap ? snapValue(v) : Math.round(v))
  const min = Math.min(MIN_SIZE, start.width, start.height)

  if (handle.includes('w')) left = clamp(s(left + dx), 0, right - min)
  if (handle.includes('e')) right = clamp(s(right + dx), left + min, screen.width)
  if (handle.includes('n')) top = clamp(s(top + dy), 0, bottom - min)
  if (handle.includes('s')) bottom = clamp(s(bottom + dy), top + min, screen.height)

  return { x: left, y: top, width: right - left, height: bottom - top }
}

/** Mueve con el teclado (sin imán: 1 px es 1 px). */
export function nudgeBox(box: Box, dx: number, dy: number, screen: Size): Box {
  return moveBox(box, dx, dy, screen, false)
}

/** Cursor CSS de cada tirador. */
export const HANDLE_CURSOR: Record<Handle, string> = {
  n: 'ns-resize',
  s: 'ns-resize',
  e: 'ew-resize',
  w: 'ew-resize',
  ne: 'nesw-resize',
  sw: 'nesw-resize',
  nw: 'nwse-resize',
  se: 'nwse-resize',
}

/** 'fit' = el mayor zoom que cabe en el hueco (preferido); o un zoom fijo. */
export type ZoomMode = 'fit' | 1 | 2 | 3
const FIT_STEPS = [3, 2.5, 2, 1.75, 1.5, 1.25, 1, 0.75, 0.5]
const PADDING = 80 // margen alrededor del lienzo (40 px por lado)

export function fitZoom(screen: { width: number; height: number }, room: { width: number; height: number }): number {
  return FIT_STEPS.find((z) => screen.width * z + PADDING <= room.width && screen.height * z + PADDING <= room.height) ?? 0.5
}
