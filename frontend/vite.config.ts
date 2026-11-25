import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 开发使用 5274；5173 留给生产静态构建。
    host: true,
    allowedHosts: true,
    port: 5274,
    strictPort: true,
    proxy: {
      // 开发期把 /api 代理到后端，避免跨域配置
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET ?? 'http://127.0.0.1:8008',
        changeOrigin: true,
      },
    },
  },
})
