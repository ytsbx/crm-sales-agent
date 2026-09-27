import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 局域网演示：监听所有网卡，并放行按 IP / 主机名进来的 Host 头（Vite 6+ 默认只认 localhost）
    host: true,
    allowedHosts: true,
    port: 5173,
    proxy: {
      // 开发期把 /api 代理到后端，避免跨域配置
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
