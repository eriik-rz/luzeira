import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// El editor compilado va dentro del paquete de Python: el servidor local de Luzeira lo
// sirve desde ahí (src/luzeira/editor/web). Rutas relativas para no depender del puerto.
//
// Desarrollo contra el servidor real:
//   LUZEIRA_API=http://127.0.0.1:<puerto> npm run dev
//   y abrir http://127.0.0.1:5173/#token=<token>
// Vite reenvía /api al servidor (sin CORS: para el navegador es el mismo origen).
// Sin servidor: http://127.0.0.1:5173/?simulado
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', 'LUZEIRA_')
  const target = env.LUZEIRA_API
  return {
    plugins: [react()],
    base: './',
    server: {
      host: '127.0.0.1', // nunca escuchar en la red local
      port: 5173,
      strictPort: true,
      proxy: target ? { '/api': { target, changeOrigin: true } } : undefined,
    },
    build: {
      outDir: '../src/luzeira/editor/web',
      emptyOutDir: true,
      sourcemap: false,
    },
  }
})
