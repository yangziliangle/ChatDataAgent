import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // dev 模式下 /api 代理到 Node 网关（server/ :3000），网关再转发 Python 核心
    proxy: {
      '/api': 'http://localhost:3000',
    },
  },
  build: {
    rollupOptions: {
      output: {
        // 拆出大依赖为独立 chunk（并行加载 + 长缓存）
        manualChunks: {
          echarts: ['echarts'],
          'react-vendor': ['react', 'react-dom'],
          markdown: ['react-markdown'],
        },
      },
    },
  },
});
