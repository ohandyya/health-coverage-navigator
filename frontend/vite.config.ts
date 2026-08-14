import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
// `defineConfig` from vitest/config rather than vite: it is the one that knows about the `test`
// key below. Importing vite's own would typecheck the config as a plain Vite config and reject it.
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, './src') },
  },
  server: {
    // Explicit IPv4 loopback. Vite's default binds `[::1]` only, so `curl 127.0.0.1:5173` is
    // refused while `localhost:5173` works — a confusing split, and inconsistent with the
    // uvicorn side, which binds 127.0.0.1 deliberately because there is no auth
    // (docs/frontend_plan.md §8). Naming the host keeps both halves on the same interface.
    host: '127.0.0.1',
    port: 5173,
    // Fail loudly instead of sliding to :5174, which from the browser looks exactly like a
    // dead API proxy.
    strictPort: true,
    proxy: {
      // This proxy is the reason there is no CORS configuration anywhere in this repo
      // (docs/frontend_plan.md §2): the browser only ever talks to :5173, so it is a single
      // origin even in development. Do not add a `configure` hook that buffers the response —
      // it would break POST /api/chat/stream.
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false },
    },
  },
  test: {
    // Only src/api/stream.ts earns a unit test (docs/frontend_plan.md §1): it is the one module
    // with logic rather than markup, and chunk-boundary bugs there are invisible until they are
    // not. Components are deliberately not tested.
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})
