import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

// 前端只请求相对路径 /api，由这里的代理转发到本机后端
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
  },
})
