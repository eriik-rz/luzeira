import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './i18n/catalogos.ts'
import App from './App.tsx'
import { IdiomaProvider } from './i18n/Idioma.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <IdiomaProvider>
      <App />
    </IdiomaProvider>
  </StrictMode>,
)
