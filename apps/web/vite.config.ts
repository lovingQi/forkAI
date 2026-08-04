import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

const gateway = process.env.VITE_GATEWAY_TARGET || 'http://127.0.0.1:19000'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': { target: gateway, changeOrigin: true },
      '/ws': { target: gateway, ws: true, changeOrigin: true }
    }
  }
})
