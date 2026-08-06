import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  // GitHub Pages serves the chat app from /FrontShiftAI/app/ (the landing page
  // owns /FrontShiftAI/). Local dev and any root-hosted deploy stay at /.
  base: process.env.GITHUB_PAGES === "true" ? "/FrontShiftAI/app/" : "/",
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      }
    }
  }
})

