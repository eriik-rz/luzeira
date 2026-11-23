// Pruebas de la lógica pura del editor (sin dependencias: node --test con los tipos
// quitados por Node 22.6+). Se ejecutan con `npm test`.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { registerCatalogs, setLanguage, type Catalog } from '../src/model/i18n.ts'

// Los textos se comparan con el español de verdad: se leen del disco los catálogos de
// `src/i18n/<código>/*.json` (sin Vite, que es quien los junta en la app).
function readCatalogs(code: string): Catalog {
  const dir = new URL(`../src/i18n/${code}/`, import.meta.url)
  const out: Catalog = {}
  if (!existsSync(dir)) return out
  for (const f of readdirSync(dir).filter((n) => n.endsWith('.json'))) {
    Object.assign(out, JSON.parse(readFileSync(new URL(f, dir), 'utf-8')) as Catalog)
  }
  return out
}
/**
 * El contrato de `pantalla.json` (lo escribe el agente de Python): días con el 0 en lunes,
 * meses del 1 al 12, las dos fechas y el separador decimal. Si el fichero ya está, manda él.
 */
const PANTALLA_ES: Catalog = {
  ...Object.fromEntries(['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo'].map((d, i) => [`pantalla.dia.${i}`, d])),
  ...Object.fromEntries(
    ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'].map((m, i) => [`pantalla.mes.${i + 1}`, m]),
  ),
  'pantalla.fecha_larga': '{dia}, {d} de {mes}',
  'pantalla.fecha_corta': '{dd}/{mm}/{aaaa}',
  'pantalla.decimal': ',',
}
const CATALOGS: Record<string, Catalog> = { es: { ...PANTALLA_ES, ...readCatalogs('es') }, en: readCatalogs('en') }
registerCatalogs(CATALOGS)
setLanguage('es')

import { clampBox, moveBox, resizeBox, boxInside, pickBelow } from '../src/model/geometry.ts'
import { reducer, initialState, gapToIndex, HISTORY_LIMIT, MERGE_WINDOW_MS, type EditorState } from '../src/model/editorState.ts'
import { normalizeTheme, createWidget, placeNewBox } from '../src/model/theme.ts'
import { validateTheme, widgetIndexOf, fieldOf } from '../src/model/validate.ts'
import {
  appliedMessage,
  encodeFilenameHeader,
  flushOutcome,
  isDroppableImage,
  isSupersededPreview,
  messageForStatus,
  pickInitialTheme,
  deletedMessage,
  applyBlock,
  displayPill,
} from '../src/model/contract.ts'
import { formatClock, formatReading } from '../src/model/format.ts'
import type { Theme, ThemeJson } from '../src/model/types.ts'

const LANDSCAPE = { width: 480, height: 320 }

const json: ThemeJson = {
  format: 'luzeira-theme/1',
  name: 'Prueba',
  orientation: 'landscape',
  widgets: [
    { type: 'text', text: 'A', box: { x: 0, y: 0, width: 100, height: 20 } },
    { type: 'text', text: 'B', box: { x: 10, y: 10, width: 100, height: 20 } },
    { type: 'text', text: 'C', box: { x: 400, y: 280, width: 80, height: 40 } },
  ],
}
const theme: Theme = normalizeTheme(json)
const texts = (s: EditorState) => s.present.theme.widgets.map((w) => (w.type === 'text' ? w.text : w.type))

// --- Geometría ---------------------------------------------------------------------

test('mover con imán deja la esquina en múltiplos de 4', () => {
  const b = moveBox({ x: 10, y: 10, width: 50, height: 30 }, 7, 3, LANDSCAPE, true)
  assert.deepEqual(b, { x: 16, y: 12, width: 50, height: 30 })
})

test('mover sin imán respeta el píxel exacto', () => {
  const b = moveBox({ x: 10, y: 10, width: 50, height: 30 }, 7, 3, LANDSCAPE, false)
  assert.deepEqual(b, { x: 17, y: 13, width: 50, height: 30 })
})

test('mover nunca saca el recuadro de la pantalla', () => {
  const b = moveBox({ x: 400, y: 300, width: 70, height: 20 }, 500, 500, LANDSCAPE, true)
  assert.deepEqual(b, { x: 410, y: 300, width: 70, height: 20 })
  assert.ok(boxInside(b, LANDSCAPE))
  const c = moveBox({ x: 4, y: 4, width: 70, height: 20 }, -50, -50, LANDSCAPE, false)
  assert.deepEqual(c, { x: 0, y: 0, width: 70, height: 20 })
})

test('redimensionar por la esquina inferior derecha con imán', () => {
  const b = resizeBox({ x: 20, y: 20, width: 100, height: 40 }, 'se', 13, 5, LANDSCAPE, true)
  assert.deepEqual(b, { x: 20, y: 20, width: 112, height: 44 })
})

test('redimensionar por la izquierda deja fijo el borde derecho', () => {
  const b = resizeBox({ x: 20, y: 20, width: 100, height: 40 }, 'w', -9, 0, LANDSCAPE, true)
  assert.equal(b.x + b.width, 120)
  assert.equal(b.x, 12)
})

test('redimensionar no cruza el borde contrario ni sale de la pantalla', () => {
  const b = resizeBox({ x: 20, y: 20, width: 100, height: 40 }, 'e', -500, 0, LANDSCAPE, false)
  assert.equal(b.width, 4)
  const c = resizeBox({ x: 400, y: 20, width: 60, height: 40 }, 'ne', 500, -500, LANDSCAPE, false)
  assert.deepEqual(c, { x: 400, y: 0, width: 80, height: 60 })
})

test('clampBox recorta lo que no cabe y lo mete dentro', () => {
  assert.deepEqual(clampBox({ x: 300, y: 10, width: 600, height: 20 }, LANDSCAPE), { x: 0, y: 10, width: 480, height: 20 })
})

// --- Reducer -------------------------------------------------------------------------

test('setBox recoloca dentro de la pantalla', () => {
  const s = reducer(initialState(theme), { type: 'setBox', id: 'w1', box: { x: 470, y: 0, width: 100, height: 20 } })
  assert.deepEqual(s.present.theme.widgets[0].box, { x: 380, y: 0, width: 100, height: 20 })
})

test('un gesto de arrastre es un solo paso de deshacer', () => {
  let s = initialState(theme)
  for (let i = 1; i <= 10; i++) {
    s = reducer(s, { type: 'setBox', id: 'w1', box: { x: i * 4, y: 0, width: 100, height: 20 }, mergeKey: 'gesture:1', at: i * 5000 })
  }
  assert.equal(s.past.length, 1)
  s = reducer(s, { type: 'undo' })
  assert.equal(s.present.theme.widgets[0].box.x, 0)
})

test('cambios con distinta clave son pasos distintos; rehacer vuelve', () => {
  let s = initialState(theme)
  s = reducer(s, { type: 'nudge', id: 'w1', dx: 1, dy: 0, at: 0 })
  s = reducer(s, { type: 'nudge', id: 'w1', dx: 1, dy: 0, at: 100 }) // se funde (misma tecla, < 1 s)
  s = reducer(s, { type: 'nudge', id: 'w2', dx: 0, dy: 1, at: 200 })
  assert.equal(s.past.length, 2)
  s = reducer(s, { type: 'undo' })
  s = reducer(s, { type: 'undo' })
  assert.equal(s.present.theme.widgets[0].box.x, 0)
  s = reducer(s, { type: 'redo' })
  assert.equal(s.present.theme.widgets[0].box.x, 2)
  // Un cambio nuevo borra lo que quedaba por rehacer.
  s = reducer(s, { type: 'remove', id: 'w3' })
  assert.equal(s.future.length, 0)
})

test('el historial no pasa del límite', () => {
  let s = initialState(theme)
  for (let i = 0; i < HISTORY_LIMIT + 30; i++) {
    s = reducer(s, { type: 'nudge', id: 'w1', dx: i % 2 ? 1 : -1, dy: 0, at: i * 10_000 })
  }
  // Exactamente el límite: cada paso cuenta (no se funden) y los más viejos se caen.
  assert.equal(s.past.length, HISTORY_LIMIT)
})

test('la misma clave separada más de la ventana de fusión son dos pasos', () => {
  let s = initialState(theme)
  s = reducer(s, { type: 'updateTheme', patch: { name: 'X' }, mergeKey: 'theme:name', at: 0 })
  s = reducer(s, { type: 'updateTheme', patch: { name: 'XY' }, mergeKey: 'theme:name', at: MERGE_WINDOW_MS })
  assert.equal(s.past.length, 1) // justo en el borde: se funde
  s = reducer(s, { type: 'updateTheme', patch: { name: 'XYZ' }, mergeKey: 'theme:name', at: 2 * MERGE_WINDOW_MS + 1 })
  assert.equal(s.past.length, 2)
  s = reducer(s, { type: 'undo' })
  assert.equal(s.present.theme.name, 'XY')
})

test('rehacer tampoco pasa del límite del historial', () => {
  let s = initialState(theme)
  for (let i = 0; i < HISTORY_LIMIT; i++) {
    s = reducer(s, { type: 'nudge', id: 'w1', dx: i % 2 ? -1 : 1, dy: 0, at: i * 10_000 })
  }
  assert.equal(s.past.length, HISTORY_LIMIT)
  // Pasado lleno y algo por rehacer a la vez: con acciones normales no se llega (deshacer
  // vacía un hueco), así que se construye a mano para probar el recorte de `redo`.
  s = { ...s, future: [s.present] }
  s = reducer(s, { type: 'redo' })
  assert.equal(s.past.length, HISTORY_LIMIT)
})

test('reordenar mueve el elemento y conserva su id', () => {
  let s = initialState(theme)
  s = reducer(s, { type: 'select', id: 'w1' })
  s = reducer(s, { type: 'reorder', from: 0, to: 2 })
  assert.deepEqual(texts(s), ['B', 'C', 'A'])
  assert.deepEqual(s.present.ids, ['w2', 'w3', 'w1'])
  assert.equal(s.selectedId, 'w1')
  s = reducer(s, { type: 'undo' })
  assert.deepEqual(texts(s), ['A', 'B', 'C'])
})

test('huecos de la lista de capas (arriba = delante)', () => {
  // Lista mostrada: C, B, A. Llevar A (tema 0) arriba del todo → al final del tema.
  assert.equal(gapToIndex(3, 0, 0), 2)
  // Llevar C (tema 2) abajo del todo → al principio del tema.
  assert.equal(gapToIndex(3, 2, 3), 0)
  // Soltar C justo donde estaba no cambia nada.
  assert.equal(gapToIndex(3, 2, 1), 2)
})

test('duplicar pone la copia encima, desplazada y seleccionada', () => {
  let s = initialState(theme)
  s = reducer(s, { type: 'duplicate', id: 'w3' })
  assert.equal(s.present.theme.widgets.length, 4)
  const copy = s.present.theme.widgets[3]
  assert.ok(boxInside(copy.box, LANDSCAPE)) // C estaba pegado a la esquina
  assert.equal(s.selectedId, s.present.ids[3])
})

test('girar la pantalla recoloca lo que ya no cabe', () => {
  let s = initialState(theme)
  s = reducer(s, { type: 'setOrientation', orientation: 'portrait' })
  assert.deepEqual(validateTheme(s.present.theme), [])
  assert.deepEqual(s.present.theme.widgets[2].box, { x: 240, y: 280, width: 80, height: 40 })
})

test('borrar quita la selección y deshacer la devuelve', () => {
  let s = initialState(theme)
  s = reducer(s, { type: 'select', id: 'w2' })
  s = reducer(s, { type: 'remove', id: 'w2' })
  assert.equal(s.selectedId, null)
  assert.deepEqual(texts(s), ['A', 'C'])
  s = reducer(s, { type: 'undo' })
  assert.deepEqual(texts(s), ['A', 'B', 'C'])
})

test('los elementos nuevos caben en ambas orientaciones', () => {
  for (const orientation of ['landscape', 'portrait'] as const) {
    const t = { ...theme, orientation, widgets: [] }
    for (const type of ['text', 'clock', 'sensor', 'bar', 'image'] as const) {
      const w = createWidget(type, t, { file: 'img/a.png', imageSize: { width: 4000, height: 100 } })
      assert.deepEqual(validateTheme({ ...t, widgets: [w] }), [], `${type} en ${orientation}`)
    }
  }
  const b = placeNewBox({ width: 100, height: 20 }, LANDSCAPE, [{ x: 192, y: 152, width: 1, height: 1 }])
  assert.deepEqual([b.x, b.y], [200, 160])
})

// --- Validación y textos ---------------------------------------------------------------

test('la validación marca lo que se sale con la ruta del servidor', () => {
  const bad = { ...theme, widgets: [{ ...theme.widgets[0], box: { x: 400, y: 0, width: 100, height: 20 } }] }
  const issues = validateTheme(bad)
  assert.equal(issues.length, 1)
  assert.equal(issues[0].path, 'widgets.0.box.width')
})

test('la validación replica rangos de modelo.py', () => {
  const w = createWidget('bar', theme)
  const issues = validateTheme({ ...theme, widgets: [{ ...w, min: 5, max: 5, fill: 'naranja' } as typeof w] })
  assert.deepEqual(issues.map((i) => i.path).sort(), ['widgets.0.fill', 'widgets.0.max'])
  assert.equal(validateTheme({ ...theme, name: '' })[0].path, 'name')
  const img = createWidget('image', theme, { file: 'img/CON.png' })
  assert.equal(validateTheme({ ...theme, widgets: [img] })[0].path, 'widgets.0.file')
})

test('textos de reloj y sensor como en Python', () => {
  const d = new Date(2026, 10, 17, 9, 5, 7) // martes
  assert.equal(formatClock('HH:MM:SS', d), '09:05:07')
  assert.equal(formatClock('date-long', d), 'martes, 17 de noviembre')
  const fields = { decimals: 1, prefix: '↓ ', suffix: '', show_unit: true, max_chars: 5 }
  assert.equal(formatReading(fields, { value: 2.26, unit: 'MB/s', text: null }), '↓ 2,3 MB/s')
  assert.equal(formatReading(fields, { value: null, unit: '%', text: null }), '↓ —')
  assert.equal(formatReading(fields, { value: null, unit: '', text: 'GeForce RTX' }), '↓ GeFo…')
})

// --- Contrato con el servidor (ronda de remate) -------------------------------------

test('403 siempre genérico; 409 usa el mensaje del servidor', () => {
  const generic = messageForStatus(403)
  assert.equal(messageForStatus(403, 'Token incorrecto'), generic)
  assert.equal(messageForStatus(409, 'Los temas incluidos no se pueden cambiar.'), 'Los temas incluidos no se pueden cambiar.')
  assert.ok(messageForStatus(409) !== generic)
  assert.equal(messageForStatus(500), 'Algo ha fallado (código 500).')
})

test('el nombre de la subida va codificado en URL (cabecera solo ASCII)', () => {
  const v = encodeFilenameHeader('Fondo de año ñ.png')
  assert.equal(v, 'Fondo%20de%20a%C3%B1o%20%C3%B1.png')
  assert.ok(/^[!-~]+$/.test(v))
  assert.equal(decodeURIComponent(v), 'Fondo de año ñ.png')
})

test('al arrancar se abre el tema de la pantalla si existe', () => {
  const list = [
    { name: 'esencial', builtin: true },
    { name: 'mi-tema', builtin: false },
    { name: 'otro', builtin: false },
  ]
  assert.equal(pickInitialTheme(list, 'otro')?.name, 'otro')
  assert.equal(pickInitialTheme(list, 'esencial')?.name, 'esencial')
  assert.equal(pickInitialTheme(list, 'borrado')?.name, 'mi-tema')
  assert.equal(pickInitialTheme(list, null)?.name, 'mi-tema')
  assert.equal(pickInitialTheme([list[0]])?.name, 'esencial')
  assert.equal(pickInitialTheme([]), undefined)
})

test('un error del recuadro entero (widgets.N.box) apunta a su elemento', () => {
  assert.equal(widgetIndexOf('widgets.2.box'), 2)
  assert.equal(fieldOf('widgets.2.box'), 'box')
  assert.equal(widgetIndexOf('widgets'), null)
  assert.equal(widgetIndexOf('widgets.12.box.x'), 12)
})

test('replaceTheme con otro número de elementos empieza de cero (los ids cuadran)', () => {
  let s = initialState(theme)
  s = reducer(s, { type: 'nudge', id: 'w1', dx: 1, dy: 0, at: 0 })
  const fewer = { ...theme, widgets: theme.widgets.slice(0, 2) }
  s = reducer(s, { type: 'replaceTheme', theme: fewer })
  assert.ok(s.present.theme === fewer)
  assert.deepEqual(s.present.ids, ['w1', 'w2'])
  assert.equal(s.past.length, 0)
})

test('updateWidget no cambia el tipo de un elemento', () => {
  const s = initialState(theme)
  const clock = createWidget('clock', theme)
  assert.ok(reducer(s, { type: 'updateWidget', id: 'w1', widget: clock }) === s)
  // Con el mismo tipo sí cambia.
  const u = reducer(s, { type: 'updateWidget', id: 'w1', widget: { ...theme.widgets[0], text: 'Z' } as typeof clock })
  assert.equal(texts(u)[0], 'Z')
})

test('deshacer un «añadir» quita la selección del elemento que ya no existe', () => {
  let s = initialState(theme)
  s = reducer(s, { type: 'add', widget: createWidget('text', theme) })
  const added = s.selectedId
  assert.ok(added !== null && s.present.ids.includes(added))
  s = reducer(s, { type: 'undo' })
  assert.equal(s.selectedId, null)
  // Deshacer con algo seleccionado que sigue existiendo lo conserva.
  s = reducer(s, { type: 'select', id: 'w2' })
  s = reducer(s, { type: 'nudge', id: 'w2', dx: 1, dy: 0, at: 0 })
  s = reducer(s, { type: 'undo' })
  assert.equal(s.selectedId, 'w2')
})

test('updateTheme sin cambios no toca el estado', () => {
  const s = initialState(theme)
  assert.ok(reducer(s, { type: 'updateTheme', patch: { name: theme.name } }) === s)
  assert.ok(reducer(s, { type: 'updateTheme', patch: { background: { ...theme.background } } }) === s)
  const t = reducer(s, { type: 'updateTheme', patch: { background: { ...theme.background, color: '#123456' } } })
  assert.equal(t.present.theme.background.color, '#123456')
  assert.equal(t.past.length, 1)
})

test('el «Deshacer» del aviso devuelve justo el elemento borrado', () => {
  let s = initialState(theme)
  const b = s.present.theme.widgets[1]
  s = reducer(s, { type: 'remove', id: 'w2' })
  // Otro cambio después del borrado: no debe perderse al recuperar.
  s = reducer(s, { type: 'nudge', id: 'w1', dx: 4, dy: 0, at: 0 })
  s = reducer(s, { type: 'restore', id: 'w2', widget: b, index: 1 })
  assert.deepEqual(texts(s), ['A', 'B', 'C'])
  assert.deepEqual(s.present.ids, ['w1', 'w2', 'w3'])
  assert.equal(s.present.theme.widgets[0].box.x, 4)
  // Si ya se recuperó (p. ej. con Ctrl+Z), pulsarlo otra vez no lo duplica.
  assert.ok(reducer(s, { type: 'restore', id: 'w2', widget: b, index: 1 }) === s)
})

test('una imagen soltada se centra en el punto y no se sale', () => {
  const w = createWidget('image', theme, { file: 'img/a.png', imageSize: { width: 40, height: 20 }, center: { x: 100, y: 50 } })
  assert.deepEqual(w.box, { x: 80, y: 40, width: 40, height: 20 })
  const edge = createWidget('image', theme, { file: 'img/a.png', imageSize: { width: 40, height: 20 }, center: { x: 479, y: 319 } })
  assert.ok(boxInside(edge.box, LANDSCAPE))
  assert.deepEqual([edge.box.x, edge.box.y], [440, 300])
})

test('replaceTheme pone el mismo objeto, conserva ids y se puede deshacer', () => {
  let s = initialState(theme)
  const ids = s.present.ids
  const renamed = { ...theme, name: 'Copia' }
  s = reducer(s, { type: 'replaceTheme', theme: renamed })
  assert.ok(s.present.theme === renamed)
  assert.deepEqual(s.present.ids, ids)
  s = reducer(s, { type: 'undo' })
  assert.equal(s.present.theme.name, 'Prueba')
})

// --- Remate 2: cierre de la ventana, vista previa superada, aplicar ---------------------

test('flush dice a la ventana qué queda sin guardar', () => {
  assert.deepEqual(flushOutcome({ dirty: false, builtin: true, invalid: true }), { saved: true, pending: 'none' })
  assert.deepEqual(flushOutcome({ dirty: true, builtin: true, invalid: false }), { saved: false, pending: 'builtin' })
  assert.deepEqual(flushOutcome({ dirty: true, builtin: false, invalid: true }), { saved: false, pending: 'invalid' })
  assert.deepEqual(flushOutcome({ dirty: true, builtin: false, invalid: false, lastSave: 'invalid' }), { saved: false, pending: 'invalid' })
  assert.deepEqual(flushOutcome({ dirty: true, builtin: false, invalid: false, lastSave: 'error' }), { saved: false, pending: 'error' })
})

test('la vista previa superada no es un error', () => {
  assert.ok(isSupersededPreview(204))
  assert.ok(isSupersededPreview(409, { superseded: true }))
  assert.ok(!isSupersededPreview(409, { error: 'Los temas incluidos no se pueden cambiar.' }))
  assert.ok(!isSupersededPreview(409, { superseded: 'true' }))
  assert.ok(!isSupersededPreview(200))
  assert.ok(!isSupersededPreview(400, { superseded: true }))
})

test('el aviso de «Aplicar» dice lo que pasa en la pantalla', () => {
  assert.equal(appliedMessage('Mío', { status: 'running', message: '' }).message, 'Listo: la pantalla muestra «Mío».')
  const none = appliedMessage('Mío', { status: 'no_display', message: 'No encuentro la pantalla.' })
  assert.equal(none.message, 'Guardado como tema de la pantalla. Se verá cuando la conectes.')
  assert.equal(appliedMessage('Mío', { status: 'theme_error', message: 'Falta la imagen.' }).kind, 'error')
  assert.ok(!appliedMessage('Mío', null).message.includes('muestra'))
})

test('solo se sueltan imágenes en el lienzo', () => {
  assert.ok(isDroppableImage('Foto.JPG'))
  assert.ok(isDroppableImage('a.png') && isDroppableImage('b.jpeg') && isDroppableImage('c.gif'))
  assert.ok(!isDroppableImage('letra.ttf'))
  assert.ok(!isDroppableImage('png'))
  assert.ok(!isDroppableImage('x.png.exe'))
})

test('Alt+clic baja una capa en el punto y vuelve arriba al llegar al fondo', () => {
  // Orden del tema: el último se ve delante (como el barrido de Retro Ámbar).
  const boxes = [
    { x: 0, y: 0, width: 100, height: 100 }, // fondo-cifra
    { x: 50, y: 50, width: 10, height: 10 }, // otro sitio
    { x: 0, y: 0, width: 480, height: 320 }, // barrido encima de todo
  ]
  const ids = ['cifra', 'lejos', 'barrido']
  const p = { x: 10, y: 10 }
  assert.equal(pickBelow(boxes, ids, p, null), 'barrido') // sin selección: el de arriba
  assert.equal(pickBelow(boxes, ids, p, 'barrido'), 'cifra') // baja una
  assert.equal(pickBelow(boxes, ids, p, 'cifra'), 'barrido') // del fondo, vuelve arriba
  assert.equal(pickBelow(boxes, ids, p, 'lejos'), 'barrido') // el seleccionado no está aquí
  assert.equal(pickBelow(boxes, ids, { x: 479, y: 319 }, 'barrido'), 'barrido') // solo uno
  assert.equal(pickBelow(boxes, ids, { x: 480, y: 10 }, null), null) // fuera de todo
})

test('al borrar el tema que estaba en pantalla, el aviso dice qué se ha puesto', () => {
  assert.equal(deletedMessage(false), 'Tema movido a la papelera de Luzeira.')
  assert.ok(/papelera.*En la pantalla se ha puesto «Esencial»/.test(deletedMessage(true)))
})

test('«Aplicar» pregunta antes solo si queda algo por guardar', () => {
  assert.equal(applyBlock({ builtin: false, dirty: false, invalid: false }), null)
  assert.equal(applyBlock({ builtin: true, dirty: false, invalid: false }), null)
  assert.equal(applyBlock({ builtin: false, dirty: true, invalid: false }), 'pending')
  assert.equal(applyBlock({ builtin: false, dirty: true, invalid: true }), 'invalid')
  assert.equal(applyBlock({ builtin: true, dirty: true, invalid: false }), 'builtin-changed')
  assert.equal(applyBlock({ builtin: true, dirty: true, invalid: true }), 'builtin-changed')
})

// --- Ventana «Ajustes» -----------------------------------------------------------------

import { AUTO, OTHER, displaySelectValue, findOption, needsConfirmation } from '../src/model/settings.ts'

const opt = (id: string | null, current = false, warning: string | null = null) => ({ id, current, warning })

test('ajustes: el selector marca «detectarla sola», la elegida o «otra»', () => {
  const choices = [opt('lista:COM3:turing-rev-a'), opt('lista:COM8:kipye-rev-d', true, 'aviso')]
  assert.equal(displaySelectValue({ display_auto: true, choices }), AUTO)
  assert.equal(displaySelectValue({ display_auto: false, choices }), 'lista:COM8:kipye-rev-d')
  // elegida en modo avanzado: no está en la lista cerrada
  assert.equal(displaySelectValue({ display_auto: false, choices: [opt('lista:COM3:x')] }), OTHER)
  // una que no se puede elegir (id null) nunca sale marcada
  assert.equal(displaySelectValue({ display_auto: false, choices: [opt(null, true)] }), OTHER)
})

test('ajustes: beta y modo avanzado piden confirmar; la rev A no', () => {
  assert.equal(needsConfirmation(opt('a', false, 'Cuidado…')), true)
  assert.equal(needsConfirmation(opt('a')), false)
})

test('ajustes: la opción se busca en las dos listas', () => {
  const closed = [opt('lista:COM3:a')]
  const advanced = [opt('avanzado:COM4:a', false, 'Cuidado')]
  assert.equal(findOption('avanzado:COM4:a', [closed, advanced]), advanced[0])
  assert.equal(findOption('nada', [closed, advanced]), null)
})

// --- C-17: letras que faltan -------------------------------------------------------------

import { missingGlyphsMessage, parseMissingGlyphs } from '../src/model/contract.ts'

test('letras que faltan: la cabecera se lee sin romper nunca', () => {
  assert.deepEqual(parseMissingGlyphs('[{"index":1,"chars":"\u2603"}]'), [{ index: 1, chars: '☃' }])
  assert.deepEqual(parseMissingGlyphs(null), [])
  assert.deepEqual(parseMissingGlyphs('no es json'), [])
  assert.deepEqual(parseMissingGlyphs('{"index":1}'), [])
  assert.deepEqual(parseMissingGlyphs('[{"index":"1","chars":"x"},{"index":2,"chars":""}]'), [])
})

test('letras que faltan: el aviso nombra la letra y el elemento', () => {
  const msg = missingGlyphsMessage([{ index: 0, chars: '★☃' }, { index: 5, chars: '€' }], ['Texto: "Hola"'])
  assert.equal(msg, '«★» «☃» en «Texto: "Hola"»; «€» en «un elemento»')
})

test('sin pantalla o con error: aviso que llama la atención; lo demás, nota discreta', () => {
  const msg = 'No encuentro la pantalla. ¿Está conectada por USB?'
  assert.deepEqual(displayPill({ status: 'no_display', message: msg }), { kind: 'alert', text: 'Sin pantalla', title: msg })
  assert.equal(displayPill({ status: 'error', message: 'x' })?.kind, 'error')
  assert.equal(displayPill({ status: 'theme_error', message: 'x' })?.kind, 'error')
  assert.equal(displayPill({ status: 'running', message: 'Mostrando «Hielo»' })?.kind, 'note')
  assert.equal(displayPill({ status: 'simulado', message: 'Modo simulado' }), null)
  assert.equal(displayPill({ status: 'running', message: '' }), null)
  assert.equal(displayPill(null), null)
})

test('idiomas: el activo, luego inglés, luego español, y si no la clave; con variables', async () => {
  const i18n = await import('../src/model/i18n.ts')
  i18n.registerCatalogs({
    es: { hola: 'Hola, {n}', solo_es: 'Solo aquí' },
    en: { hola: 'Hi, {n}' },
    de: { hola: 'Hallo, {n}' },
  })
  assert.deepEqual(i18n.availableLanguages(), ['es', 'en', 'de'])
  assert.equal(i18n.setLanguage('de'), 'de')
  assert.equal(i18n.t('hola', { n: 'Ana' }), 'Hallo, Ana')
  assert.equal(i18n.t('solo_es'), 'Solo aquí')
  assert.equal(i18n.t('no.existe'), 'no.existe')
  assert.equal(i18n.t('hola', { otra: 1 }), 'Hallo, {n}')
  assert.equal(i18n.setLanguage(null, 'en-GB'), 'en') // automático: el del sistema
  assert.equal(i18n.setLanguage(null, 'ja-JP'), 'en') // uno que no tenemos: inglés
  assert.equal(i18n.setLanguage('xx', 'de-DE'), 'de')
  i18n.registerCatalogs(CATALOGS)
  i18n.setLanguage('es')
})

// --- C-62: idiomas en la vista previa y traducciones de los temas ----------------------

/** Un `pantalla` inglés de prueba (el de verdad lo escribe el agente de Python). */
const PANTALLA_EN: Catalog = {
  ...Object.fromEntries(['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'].map((d, i) => [`pantalla.dia.${i}`, d])),
  ...Object.fromEntries(
    ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'].map((m, i) => [`pantalla.mes.${i + 1}`, m]),
  ),
  'pantalla.fecha_larga': '{dia}, {mes} {d}',
  'pantalla.fecha_corta': '{aaaa}-{mm}-{dd}',
  'pantalla.decimal': '.',
}

/** Ejecuta `fn` en inglés con el `pantalla` de prueba y vuelve al español. */
function inEnglish(fn: () => void) {
  registerCatalogs({ ...CATALOGS, en: { ...PANTALLA_EN, ...CATALOGS.en } }) // manda el del disco
  setLanguage('en')
  try {
    fn()
  } finally {
    registerCatalogs(CATALOGS)
    setLanguage('es')
  }
}

test('reloj: el domingo de JS (0) es el último día del catálogo (0 = lunes)', () => {
  const sunday = new Date(2026, 10, 22, 12, 0, 0)
  assert.equal(formatClock('weekday', sunday), 'domingo')
  assert.equal(formatClock('weekday', new Date(2026, 10, 16)), 'lunes')
  assert.equal(formatClock('date-short', new Date(2026, 0, 5)), '05/01/2026')
})

test('reloj y sensor en inglés: días, meses, formato de fecha y punto decimal', () => {
  inEnglish(() => {
    const d = new Date(2026, 10, 17, 9, 5, 7) // martes
    assert.equal(formatClock('date-long', d), 'Tuesday, November 17')
    assert.equal(formatClock('weekday', new Date(2026, 10, 22)), 'Sunday')
    assert.equal(formatClock('date-short', d), '2026-11-17')
    assert.equal(formatClock('HH:MM', d), '09:05')
    const fields = { decimals: 1, prefix: '↓ ', suffix: '', show_unit: true, max_chars: 5 }
    assert.equal(formatReading(fields, { value: 2.26, unit: 'MB/s', text: null }), '↓ 2.3 MB/s')
  })
  // Y de vuelta en español, la coma.
  assert.equal(formatReading({ decimals: 1, prefix: '', suffix: '', show_unit: false, max_chars: 5 }, { value: 2.26, unit: '', text: null }), '2,3')
})

test('sensor: el prefijo y el sufijo salen en el idioma activo si el tema los trae', () => {
  const fields = { decimals: 0, prefix: 'RED ', suffix: ' ya', prefix_i18n: { en: 'NET ' }, suffix_i18n: { de: ' jetzt' }, show_unit: true, max_chars: 40 }
  const r = { value: 3, unit: 'MB/s', text: null }
  assert.equal(formatReading(fields, r), 'RED 3 MB/s ya')
  inEnglish(() => assert.equal(formatReading(fields, r), 'NET 3 MB/s ya'))
})

const I18N_THEME: ThemeJson = {
  format: 'luzeira-theme/1',
  name: 'Con idiomas',
  orientation: 'landscape',
  widgets: [
    { type: 'text', text: 'RED', text_i18n: { en: 'NET', de: 'NETZ' }, box: { x: 0, y: 0, width: 100, height: 20 } },
    { type: 'sensor', sensor: 'cpu.usage', prefix: 'Uso ', prefix_i18n: { en: 'Use ' }, suffix_i18n: { en: '!' }, box: { x: 0, y: 30, width: 100, height: 20 } },
    { type: 'text', text: 'Sin', box: { x: 0, y: 60, width: 100, height: 20 } },
  ],
}

test('normalizeTheme conserva las traducciones y las vuelve a escribir tal cual', () => {
  const t = normalizeTheme(I18N_THEME)
  const [text, sensor, plain] = t.widgets
  assert.ok(text.type === 'text' && sensor.type === 'sensor' && plain.type === 'text')
  assert.deepEqual(text.text_i18n, { en: 'NET', de: 'NETZ' })
  assert.deepEqual(sensor.prefix_i18n, { en: 'Use ' })
  assert.deepEqual(sensor.suffix_i18n, { en: '!' })
  // Un texto sin traducciones se guarda igual que antes: sin el campo.
  assert.equal('text_i18n' in plain, false)
  // Lo que se guarda (JSON) lleva las traducciones: «Guardar como…» no las pierde.
  const saved = JSON.parse(JSON.stringify(t)) as ThemeJson
  assert.deepEqual(normalizeTheme(saved), t)
  assert.deepEqual(validateTheme(t), [])
  // `null` (lo que admite Python) es lo mismo que no traer nada.
  const nulls = normalizeTheme({ ...I18N_THEME, widgets: [{ type: 'text', text: 'x', text_i18n: null, box: { x: 0, y: 0, width: 10, height: 10 } }] })
  assert.equal('text_i18n' in nulls.widgets[0], false)
})

test('validate: códigos de idioma de dos letras, 20 como mucho, 200 y 20 caracteres', () => {
  const base = normalizeTheme(I18N_THEME)
  const withText = (text_i18n: Record<string, string>) => ({ ...base, widgets: [{ ...base.widgets[0], text_i18n }] as Theme['widgets'] })
  const paths = (t: Theme) => validateTheme(t).map((i) => i.path)
  assert.deepEqual(paths(withText({ EN: 'x' })), ['widgets.0.text_i18n'])
  assert.deepEqual(paths(withText({ eng: 'x' })), ['widgets.0.text_i18n'])
  assert.deepEqual(paths(withText({ en: 'x'.repeat(200) })), [])
  assert.deepEqual(paths(withText({ en: 'x'.repeat(201) })), ['widgets.0.text_i18n'])
  const many = Object.fromEntries(Array.from({ length: 21 }, (_, i) => [`a${String.fromCharCode(97 + i)}`, 'x']))
  assert.deepEqual(paths(withText(many)), ['widgets.0.text_i18n'])
  assert.deepEqual(paths(withText(Object.fromEntries(Object.entries(many).slice(0, 20)))), [])
  const s = base.widgets[1]
  assert.ok(s.type === 'sensor')
  const sensorWith = (prefix_i18n: Record<string, string>) => ({ ...base, widgets: [{ ...s, prefix_i18n }] as Theme['widgets'] })
  assert.deepEqual(paths(sensorWith({ en: 'x'.repeat(20) })), [])
  assert.deepEqual(paths(sensorWith({ en: 'x'.repeat(21) })), ['widgets.0.prefix_i18n'])
  assert.ok(validateTheme(withText({ EN: 'x' }))[0].message.includes('«EN»'))
})

import { editTranslatable, localized, translationMode } from '../src/model/format.ts'
import type { Translations } from '../src/model/types.ts'

test('propiedades: el campo traducido se edita en el idioma que se ve (C-62)', () => {
  const text: { type: 'text'; text: string; text_i18n?: Translations } = { type: 'text', text: 'RED', text_i18n: { en: 'NET', de: 'NETZ' } }
  // En inglés el campo enseña «NET» (lo mismo que el lienzo) y solo cambia esa traducción.
  assert.equal(localized(text.text, text.text_i18n, 'en'), 'NET')
  assert.equal(translationMode(text.text_i18n, 'en'), 'translation')
  const english = editTranslatable(text, 'text', 'NETWORK', 'en')
  assert.deepEqual(english, { type: 'text', text: 'RED', text_i18n: { en: 'NETWORK', de: 'NETZ' } })
  assert.deepEqual(text.text_i18n, { en: 'NET', de: 'NETZ' }) // no toca el que recibe
  // En un idioma sin traducción cambia el original y se quitan las de ese campo.
  assert.equal(translationMode(text.text_i18n, 'es'), 'drops')
  assert.deepEqual(editTranslatable(text, 'text', 'RED LOCAL', 'es'), { type: 'text', text: 'RED LOCAL' })
  // Sin traducciones, el original y nada más.
  assert.equal(translationMode(undefined, 'en'), 'base')
  assert.equal(translationMode({}, 'en'), 'base')
  assert.deepEqual(editTranslatable({ type: 'text', text: 'A' }, 'text', 'B', 'en'), { type: 'text', text: 'B' })
  // «constructor» no es un idioma del tema aunque `in` diga que está.
  assert.equal(translationMode({ en: 'x' }, 'constructor'), 'drops')
})

test('propiedades: «Antes» y «Después» van cada uno con sus traducciones', () => {
  const sensor: { type: 'sensor'; prefix: string; suffix: string; prefix_i18n?: Translations; suffix_i18n?: Translations } = {
    type: 'sensor',
    prefix: 'CPU ',
    suffix: ' uso',
    prefix_i18n: { en: 'CPU ' },
    suffix_i18n: { de: ' Last' },
  }
  // En inglés: el prefijo tiene inglés (cambia solo ese); el sufijo no (cambia el original y pierde el alemán).
  const prefix = editTranslatable(sensor, 'prefix', 'Processor ', 'en')
  assert.deepEqual(prefix.prefix_i18n, { en: 'Processor ' })
  assert.equal(prefix.prefix, 'CPU ')
  assert.deepEqual(prefix.suffix_i18n, { de: ' Last' })
  const suffix = editTranslatable(sensor, 'suffix', ' load', 'en')
  assert.equal(suffix.suffix, ' load')
  assert.ok(!('suffix_i18n' in suffix))
  assert.deepEqual(suffix.prefix_i18n, { en: 'CPU ' })
})
