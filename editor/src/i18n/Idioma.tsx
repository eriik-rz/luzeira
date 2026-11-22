// El idioma en React: cambiarlo vuelve a pintar toda la interfaz con los textos nuevos.
// Quien manda es Luzeira (`/api/info` trae `language`); en la demo, la API simulada.

import { useCallback, useState, type ReactNode } from 'react'
import { currentLanguage, setLanguage } from '../model/i18n.ts'
import { LangContext } from './contexto.ts'

export function IdiomaProvider({ children }: { children: ReactNode }) {
  const [lang, setLang] = useState(() => setLanguage(null, navigator.language))
  const follow = useCallback((code: string | null | undefined) => {
    if (!code || code === currentLanguage()) return
    setLang(setLanguage(code, navigator.language))
    document.documentElement.lang = currentLanguage()
  }, [])
  return <LangContext.Provider value={{ lang, follow }}>{children}</LangContext.Provider>
}
