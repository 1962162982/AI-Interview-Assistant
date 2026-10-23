import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 交互层构建配置（Vue 3 + Vite）。
// 前后端分离：开发期由 VITE_API_BASE_URL 指向 FastAPI（默认 http://127.0.0.1:8000）。
// 接口路径与字段以 docs/api.md 冻结版为准，此处不预设任何代理规则。
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
