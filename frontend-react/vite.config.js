import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Two ways to run the UI:
//   npm run dev    -> :5173 with hot reload, API calls proxied to :8000
//   npm run build  -> dist/, which FastAPI serves itself at :8000 (one port)
//
// IMPORTANT: every backend route prefix must appear here. A missing one is a
// silent failure that only shows up in dev - the request never leaves Vite, so
// the browser reports it as unreachable rather than as a 404. /chat and /jobs
// were missed when they were added, which is exactly how that goes wrong.
const API_ROUTES = [
  'analyze', 'chat', 'jobs', 'history', 'analysis', 'compare',
  'providers', 'health', 'extract', 'sample',
]

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',       // loopback only, same as the backend
    port: 5173,
    proxy: {
      // One regex instead of one entry per route, so the whole list is in a
      // single place and a route cannot be half-configured.
      [`^/(${API_ROUTES.join('|')})(/|$|\\?)`]: {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
