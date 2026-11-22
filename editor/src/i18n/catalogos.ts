// Junta los catálogos de `src/i18n/<código>/<zona>.json` (Vite los mete en el build) y los
// registra. Se importa una vez, en `main.tsx`, antes de pintar nada.

import { registerCatalogs, type Catalog } from '../model/i18n.ts'

const files = import.meta.glob<Catalog>('./*/*.json', { eager: true, import: 'default' })

const catalogs: Record<string, Catalog> = {}
for (const [path, data] of Object.entries(files)) {
  const code = path.split('/')[1]
  catalogs[code] = { ...catalogs[code], ...data }
}
registerCatalogs(catalogs)
