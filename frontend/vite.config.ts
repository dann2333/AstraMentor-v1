import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    // 前端现在用同源的 /api，开发时由这里转发给后端，
    // 这样 dev 和容器里跑的是同一套路径，不用分两种配置。
    proxy: {
      '/api': {
        target: process.env.ASTRA_DEV_API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
