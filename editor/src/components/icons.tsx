// Iconos en SVG en línea (sin librerías ni recursos externos). Trazo de 1,75 px a 24×24.
// Son decorativos: el texto o el `aria-label` del botón dice qué hace.

import type { ReactNode } from 'react'
import type { WidgetType } from '../model/types.ts'

function Svg({ children }: { children: ReactNode }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {children}
    </svg>
  )
}

export const IconText = () => <Svg><path d="M5 6V4h14v2M12 4v16M9 20h6" /></Svg>
export const IconClock = () => <Svg><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></Svg>
export const IconSensor = () => <Svg><path d="M3 12h4l2.5-6 5 12 2.5-6h4" /></Svg>
export const IconBar = () => <Svg><rect x="3" y="9" width="18" height="6" rx="1.5" /><path d="M6 12h7" strokeWidth="3" /></Svg>
export const IconImage = () => <Svg><rect x="3.5" y="4.5" width="17" height="15" rx="2" /><circle cx="9" cy="10" r="1.6" /><path d="m20 16-4.5-4.5L7 19.5" /></Svg>
export const IconPlus = () => <Svg><path d="M12 5v14M5 12h14" /></Svg>
export const IconUndo = () => <Svg><path d="M9 14 4 9l5-5" /><path d="M4 9h10a6 6 0 0 1 0 12h-3" /></Svg>
export const IconRedo = () => <Svg><path d="m15 14 5-5-5-5" /><path d="M20 9H10a6 6 0 0 0 0 12h3" /></Svg>
export const IconCopy = () => <Svg><rect x="8.5" y="8.5" width="11" height="11" rx="2" /><path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5" /></Svg>
export const IconTrash = () => <Svg><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12.5h10L18 7M9 7V4.5h6V7" /></Svg>
export const IconGrip = () => <Svg><circle cx="9" cy="6" r="1" /><circle cx="15" cy="6" r="1" /><circle cx="9" cy="12" r="1" /><circle cx="15" cy="12" r="1" /><circle cx="9" cy="18" r="1" /><circle cx="15" cy="18" r="1" /></Svg>
export const IconUp = () => <Svg><path d="m6 15 6-6 6 6" /></Svg>
export const IconDown = () => <Svg><path d="m6 9 6 6 6-6" /></Svg>
export const IconSend = () => <Svg><rect x="3" y="5" width="18" height="12" rx="2" /><path d="M8 21h8M12 17v4M9.5 11l2 2 3.5-4" /></Svg>
export const IconSaveAs = () => <Svg><path d="M5 4h11l3 3v13H5z" /><path d="M8 4v5h7V4M12 13v5M9.5 15.5h5" /></Svg>
export const IconWarn = () => <Svg><path d="M12 4 2.8 20h18.4z" /><path d="M12 10v4.5M12 17.5v.01" /></Svg>
export const IconInfo = () => <Svg><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5M12 8v.01" /></Svg>
export const IconGrid = () => <Svg><path d="M4 4h16v16H4zM4 9.3h16M4 14.7h16M9.3 4v16M14.7 4v16" /></Svg>
export const IconMore = () => <Svg><circle cx="6" cy="12" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="18" cy="12" r="1" /></Svg>
export const IconUpload = () => <Svg><path d="M12 16V4M7 9l5-5 5 5M4 16v4h16v-4" /></Svg>
export const IconAlignLeft = () => <Svg><path d="M4 6h16M4 10h10M4 14h16M4 18h10" /></Svg>
export const IconAlignCenter = () => <Svg><path d="M4 6h16M7 10h10M4 14h16M7 18h10" /></Svg>
export const IconAlignRight = () => <Svg><path d="M4 6h16M10 10h10M4 14h16M10 18h10" /></Svg>
export const IconCenterH = () => <Svg><path d="M12 3v18M7 8h10v8H7z" /></Svg>
export const IconCenterV = () => <Svg><path d="M3 12h18M8 7h8v10H8z" /></Svg>

export function BrandMark() {
  return (
    // El lucero de Luzeira: el mismo dibujo que src/luzeira/marca.py (icono y bandeja).
    <svg className="brand-mark" viewBox="0 0 24 24" aria-hidden="true">
      <rect x="0.75" y="0.75" width="22.5" height="22.5" rx="5" fill="#12182c" />
      <path d="M12 3.4C12.6 9.2 14.8 11.4 20.6 12C14.8 12.6 12.6 14.8 12 20.6C11.4 14.8 9.2 12.6 3.4 12C9.2 11.4 11.4 9.2 12 3.4Z" fill="#ffb02e" />
      <path d="M18 4.2C18.15 5.4 18.6 5.85 19.8 6C18.6 6.15 18.15 6.6 18 7.8C17.85 6.6 17.4 6.15 16.2 6C17.4 5.85 17.85 5.4 18 4.2Z" fill="#ffb02e" opacity="0.9" />
    </svg>
  )
}

export function WidgetIcon({ type }: { type: WidgetType }) {
  switch (type) {
    case 'text':
      return <IconText />
    case 'clock':
      return <IconClock />
    case 'sensor':
      return <IconSensor />
    case 'bar':
      return <IconBar />
    case 'image':
      return <IconImage />
  }
}
