// Bocadillos de ayuda con el estilo del editor, en lugar de los nativos de Windows
// (pequeños, amarillentos y lentos). No hace falta tocar cada botón: se sigue escribiendo
// `title="…"` y este componente, montado una vez, lo enseña a su manera.
//
// Cómo: al pasar el ratón (o llegar con el teclado) a un elemento con `title`, el texto se
// mueve a `data-tip` (así el navegador no pinta el suyo) y queda como descripción accesible
// (`aria-description`). Tras una pausa corta se muestra el bocadillo junto al elemento.

import { useEffect, useRef, useState } from 'react'

const SHOW_DELAY_MS = 350

interface Tip {
  text: string
  x: number
  y: number
  below: boolean
}

function adopt(el: HTMLElement): string | null {
  const title = el.getAttribute('title')
  if (title) {
    el.setAttribute('data-tip', title)
    if (!el.hasAttribute('aria-label') && !el.hasAttribute('aria-description')) {
      el.setAttribute('aria-description', title)
    }
    el.removeAttribute('title')
  }
  return el.getAttribute('data-tip')
}

function owner(target: EventTarget | null): HTMLElement | null {
  return target instanceof Element ? (target.closest('[title], [data-tip]') as HTMLElement | null) : null
}

export function Tooltips() {
  const [tip, setTip] = useState<Tip | null>(null)
  const timer = useRef<number | null>(null)
  const current = useRef<HTMLElement | null>(null)

  useEffect(() => {
    const clear = () => {
      if (timer.current !== null) window.clearTimeout(timer.current)
      timer.current = null
      current.current = null
      setTip(null)
    }
    const enter = (e: Event) => {
      const el = owner(e.target)
      if (!el || el === current.current) return
      const text = adopt(el)
      clear()
      if (!text) return
      current.current = el
      timer.current = window.setTimeout(() => {
        if (!el.isConnected) return
        const r = el.getBoundingClientRect()
        const below = r.top < 60 // arriba del todo no cabe por encima
        setTip({ text, x: r.left + r.width / 2, y: below ? r.bottom + 8 : r.top - 8, below })
      }, SHOW_DELAY_MS)
    }
    const leave = (e: Event) => {
      const el = current.current
      const to = (e as MouseEvent).relatedTarget
      if (el && to instanceof Node && el.contains(to)) return
      clear()
    }
    document.addEventListener('pointerover', enter)
    document.addEventListener('pointerout', leave)
    document.addEventListener('focusin', enter)
    document.addEventListener('focusout', clear)
    document.addEventListener('pointerdown', clear, true)
    document.addEventListener('keydown', clear, true)
    window.addEventListener('scroll', clear, true)
    return () => {
      clear()
      document.removeEventListener('pointerover', enter)
      document.removeEventListener('pointerout', leave)
      document.removeEventListener('focusin', enter)
      document.removeEventListener('focusout', clear)
      document.removeEventListener('pointerdown', clear, true)
      document.removeEventListener('keydown', clear, true)
      window.removeEventListener('scroll', clear, true)
    }
  }, [])

  if (!tip) return null
  // Dentro de la ventana: el bocadillo nunca se sale por los lados.
  const left = Math.min(Math.max(tip.x, 140), window.innerWidth - 140)
  return (
    <div
      className={`tooltip${tip.below ? ' below' : ''}`}
      role="tooltip"
      style={{ left, top: tip.y }}
    >
      {tip.text}
    </div>
  )
}
