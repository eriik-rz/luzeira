// El contexto del idioma, aparte del proveedor (`Idioma.tsx`) para que el recargado en
// caliente de Vite funcione (un fichero de componentes solo exporta componentes).

import { createContext, useContext } from 'react'
import { t } from '../model/i18n.ts'

export interface LangState {
  lang: string
  /** Pone el idioma que dice Luzeira (o la demo). */
  follow: (code: string | null | undefined) => void
}

export const LangContext = createContext<LangState>({ lang: 'es', follow: () => {} })

/** `t` para usar en un componente: al cambiar de idioma, el componente se vuelve a pintar. */
export function useT(): typeof t {
  useContext(LangContext)
  return t
}

export function useIdioma(): LangState {
  return useContext(LangContext)
}
