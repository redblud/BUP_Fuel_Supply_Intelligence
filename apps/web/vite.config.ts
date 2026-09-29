import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // In Docker this is http://backend:8000; locally the backend runs on :8000.
      '/api': process.env.VITE_API_PROXY_TARGET ?? 'http://127.0.0.1:8000',
    },
  },
})
