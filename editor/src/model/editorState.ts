// Estado central del editor: el documento (tema + identificadores estables de cada
// elemento), la selección y el historial de deshacer/rehacer.
//
// El historial guarda estados inmutables completos (no «diferencias»): un tema pesa
// unos pocos KB y 100 copias caben de sobra. Los cambios seguidos del mismo gesto
// (arrastrar, escribir en un campo, pulsar flechas) se funden en un solo paso con
// `mergeKey`, para que Ctrl+Z deshaga el gesto entero y no píxel a píxel.

import { clampBox, nudgeBox } from './geometry.ts'
import { duplicateWidget } from './theme.ts'
import { MAX_WIDGETS, screenSize, type Box, type Orientation, type Theme, type Widget } from './types.ts'

export const HISTORY_LIMIT = 100
/** Dos cambios con la misma `mergeKey` separados menos que esto se funden. */
export const MERGE_WINDOW_MS = 1000

export interface Doc {
  theme: Theme
  ids: string[] // ids[i] identifica a theme.widgets[i]; sobrevive a reordenar
}

export interface EditorState {
  past: Doc[]
  present: Doc
  future: Doc[]
  selectedId: string | null
  /** Clave y hora del último cambio, para fundir los del mismo gesto. */
  lastMerge: { key: string; at: number } | null
  nextId: number
}

export type Action =
  | { type: 'load'; theme: Theme }
  | { type: 'select'; id: string | null }
  | { type: 'setBox'; id: string; box: Box; mergeKey?: string; at?: number }
  | { type: 'nudge'; id: string; dx: number; dy: number; at?: number }
  | { type: 'updateWidget'; id: string; widget: Widget; mergeKey?: string; at?: number }
  | { type: 'updateTheme'; patch: Partial<Pick<Theme, 'name' | 'author' | 'background'>>; mergeKey?: string; at?: number }
  /** Pone este tema tal cual (mismo objeto) conservando los ids: «Guardar como». */
  | { type: 'replaceTheme'; theme: Theme }
  | { type: 'setOrientation'; orientation: Orientation }
  | { type: 'add'; widget: Widget }
  | { type: 'duplicate'; id: string }
  | { type: 'remove'; id: string }
  /**
   * Devuelve a su sitio un elemento borrado (el «Deshacer» del aviso de borrado). Es un
   * paso nuevo del historial, así que funciona aunque después haya habido otros cambios;
   * si ese id ya está en el tema (p. ej. ya se deshizo con Ctrl+Z), no hace nada.
   */
  | { type: 'restore'; id: string; widget: Widget; index: number }
  | { type: 'reorder'; from: number; to: number }
  | { type: 'undo' }
  | { type: 'redo' }

const emptyTheme: Theme = {
  format: 'luzeira-theme/1',
  name: '',
  author: null,
  orientation: 'landscape',
  background: { color: '#000000', image: null },
  widgets: [],
}

export function initialState(theme: Theme = emptyTheme): EditorState {
  return {
    past: [],
    present: { theme, ids: theme.widgets.map((_, i) => `w${i + 1}`) },
    future: [],
    selectedId: null,
    lastMerge: null,
    nextId: theme.widgets.length + 1,
  }
}

export function indexOfId(doc: Doc, id: string | null): number {
  return id === null ? -1 : doc.ids.indexOf(id)
}

export function selectedWidget(state: EditorState): Widget | null {
  const i = indexOfId(state.present, state.selectedId)
  return i >= 0 ? state.present.theme.widgets[i] : null
}

/**
 * Aplica un documento nuevo al historial. Si `mergeKey` coincide con el último cambio
 * y llega dentro de la ventana, reemplaza el presente sin añadir un paso.
 */
function commit(
  state: EditorState,
  doc: Doc,
  extra: Partial<EditorState> = {},
  mergeKey?: string,
  at = Date.now(),
): EditorState {
  if (doc === state.present) return { ...state, ...extra }
  const merge =
    mergeKey !== undefined &&
    state.lastMerge !== null &&
    state.lastMerge.key === mergeKey &&
    // Un gesto de ratón («gesture:…») se funde entero aunque haya pausas largas.
    (mergeKey.startsWith('gesture:') || at - state.lastMerge.at <= MERGE_WINDOW_MS) &&
    state.past.length > 0
  const past = merge ? state.past : [...state.past, state.present].slice(-HISTORY_LIMIT)
  return {
    ...state,
    ...extra,
    past,
    present: doc,
    future: [],
    lastMerge: mergeKey !== undefined ? { key: mergeKey, at } : null,
  }
}

function withWidgets(doc: Doc, widgets: Widget[], ids = doc.ids): Doc {
  return { theme: { ...doc.theme, widgets }, ids }
}

function replaceAt<T>(list: T[], i: number, value: T): T[] {
  const copy = list.slice()
  copy[i] = value
  return copy
}

export function reducer(state: EditorState, action: Action): EditorState {
  const doc = state.present
  const screen = screenSize(doc.theme)
  switch (action.type) {
    case 'load':
      return initialState(action.theme)

    case 'select':
      return { ...state, selectedId: action.id, lastMerge: null }

    case 'setBox': {
      const i = indexOfId(doc, action.id)
      if (i < 0) return state
      const w = doc.theme.widgets[i]
      const box = clampBox(action.box, screen)
      if (sameBox(box, w.box)) return state
      return commit(state, withWidgets(doc, replaceAt(doc.theme.widgets, i, { ...w, box })), {}, action.mergeKey, action.at)
    }

    case 'nudge': {
      const i = indexOfId(doc, action.id)
      if (i < 0) return state
      const w = doc.theme.widgets[i]
      const box = nudgeBox(w.box, action.dx, action.dy, screen)
      if (sameBox(box, w.box)) return state
      return commit(state, withWidgets(doc, replaceAt(doc.theme.widgets, i, { ...w, box })), {}, `nudge:${action.id}`, action.at)
    }

    case 'updateWidget': {
      const i = indexOfId(doc, action.id)
      if (i < 0 || action.widget.type !== doc.theme.widgets[i].type) return state
      return commit(state, withWidgets(doc, replaceAt(doc.theme.widgets, i, action.widget)), {}, action.mergeKey, action.at)
    }

    case 'updateTheme': {
      // Sin cambios reales no se crea un objeto nuevo: si no, se dispararían el guardado
      // automático y la vista previa para nada.
      if (samePatch(doc.theme, action.patch)) return state
      return commit(state, { ...doc, theme: { ...doc.theme, ...action.patch } }, {}, action.mergeKey, action.at)
    }

    case 'replaceTheme':
      // Solo vale si los elementos son los mismos (si no, los ids dejarían de cuadrar).
      if (action.theme.widgets.length !== doc.theme.widgets.length) return initialState(action.theme)
      return commit(state, { ...doc, theme: action.theme })

    case 'setOrientation': {
      if (action.orientation === doc.theme.orientation) return state
      // Al girar, los elementos que ya no caben se recolocan dentro (si no, el tema
      // dejaría de ser válido y no se podría guardar).
      const next = { ...doc.theme, orientation: action.orientation }
      const nextScreen = screenSize(next)
      next.widgets = doc.theme.widgets.map((w) => {
        const box = clampBox(w.box, nextScreen)
        return sameBox(box, w.box) ? w : { ...w, box }
      })
      return commit(state, { ...doc, theme: next })
    }

    case 'add': {
      if (doc.theme.widgets.length >= MAX_WIDGETS) return state
      const id = `w${state.nextId}`
      const widget = { ...action.widget, box: clampBox(action.widget.box, screen) }
      return commit(
        state,
        withWidgets(doc, [...doc.theme.widgets, widget], [...doc.ids, id]),
        { selectedId: id, nextId: state.nextId + 1 },
      )
    }

    case 'duplicate': {
      const i = indexOfId(doc, action.id)
      if (i < 0 || doc.theme.widgets.length >= MAX_WIDGETS) return state
      const id = `w${state.nextId}`
      const copy = duplicateWidget(doc.theme.widgets[i], screen)
      // La copia va justo encima del original (en el orden de capas).
      const widgets = [...doc.theme.widgets.slice(0, i + 1), copy, ...doc.theme.widgets.slice(i + 1)]
      const ids = [...doc.ids.slice(0, i + 1), id, ...doc.ids.slice(i + 1)]
      return commit(state, withWidgets(doc, widgets, ids), { selectedId: id, nextId: state.nextId + 1 })
    }

    case 'remove': {
      const i = indexOfId(doc, action.id)
      if (i < 0) return state
      const widgets = doc.theme.widgets.filter((_, j) => j !== i)
      const ids = doc.ids.filter((_, j) => j !== i)
      return commit(state, withWidgets(doc, widgets, ids), {
        selectedId: state.selectedId === action.id ? null : state.selectedId,
      })
    }

    case 'restore': {
      if (doc.ids.includes(action.id) || doc.theme.widgets.length >= MAX_WIDGETS) return state
      const at = Math.max(0, Math.min(doc.theme.widgets.length, action.index))
      const widgets = [...doc.theme.widgets.slice(0, at), action.widget, ...doc.theme.widgets.slice(at)]
      const ids = [...doc.ids.slice(0, at), action.id, ...doc.ids.slice(at)]
      return commit(state, withWidgets(doc, widgets, ids), { selectedId: action.id })
    }

    case 'reorder': {
      // `from` y `to` son posiciones en theme.widgets (0 = se pinta primero = al fondo).
      const n = doc.theme.widgets.length
      const { from } = action
      const to = Math.max(0, Math.min(n - 1, action.to))
      if (from < 0 || from >= n || from === to) return state
      const widgets = doc.theme.widgets.slice()
      const ids = doc.ids.slice()
      const [w] = widgets.splice(from, 1)
      const [id] = ids.splice(from, 1)
      widgets.splice(to, 0, w)
      ids.splice(to, 0, id)
      return commit(state, withWidgets(doc, widgets, ids))
    }

    case 'undo': {
      if (state.past.length === 0) return state
      const previous = state.past[state.past.length - 1]
      return {
        ...state,
        past: state.past.slice(0, -1),
        present: previous,
        future: [state.present, ...state.future],
        selectedId: keepSelection(previous, state.selectedId),
        lastMerge: null,
      }
    }

    case 'redo': {
      if (state.future.length === 0) return state
      const [next, ...rest] = state.future
      return {
        ...state,
        past: [...state.past, state.present].slice(-HISTORY_LIMIT),
        present: next,
        future: rest,
        selectedId: keepSelection(next, state.selectedId),
        lastMerge: null,
      }
    }
  }
}

/**
 * Traduce un hueco de la lista de capas a una posición del tema.
 * La lista se muestra al revés que el tema (arriba = delante = último del tema), así
 * que el hueco `gap` (0 = encima de la primera fila, n = debajo de la última) deja
 * delante de él `n - gap` elementos del tema. Devuelve el `to` para la acción
 * `reorder` una vez quitado el elemento que se mueve.
 */
export function gapToIndex(n: number, from: number, gap: number): number {
  const target = n - Math.max(0, Math.min(n, gap))
  return from < target ? target - 1 : target
}

function keepSelection(doc: Doc, id: string | null): string | null {
  return id !== null && doc.ids.includes(id) ? id : null
}

function samePatch(theme: Theme, patch: Partial<Pick<Theme, 'name' | 'author' | 'background'>>): boolean {
  return (Object.keys(patch) as (keyof typeof patch)[]).every((k) => {
    const value = patch[k]
    if (k === 'background') {
      const b = value as Theme['background'] | undefined
      return b === theme.background || (b !== undefined && b.color === theme.background.color && b.image === theme.background.image)
    }
    return value === theme[k]
  })
}

function sameBox(a: Box, b: Box): boolean {
  return a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height
}
