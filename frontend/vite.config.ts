import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: {
    chunkSizeWarningLimit: 1100,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined;
          // 把 antd 本体与其内部依赖（rc-*、@rc-component、@ant-design/*）放在同一个 chunk，
          // 避免循环依赖警告；图标包同样归入 antd 以消除 antd <-> icons 循环。
          if (
            id.includes('/antd/') ||
            id.includes('/@ant-design/') ||
            id.includes('/rc-') ||
            id.includes('@rc-component')
          ) {
            return 'antd';
          }
          if (id.includes('react-router')) return 'react-router';
          if (id.includes('/react-dom/') || id.match(/node_modules\/react\//)) return 'react-vendor';
          if (id.includes('dayjs') || id.includes('axios')) return 'utils';
          return undefined;
        },
      },
    },
  },
  server: {
    port: 3000,
    host: true,
    allowedHosts: ['.trycloudflare.com', '23.236.66.33'],
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
  preview: {
    port: 3000,
    host: true,
    allowedHosts: ['.trycloudflare.com', '23.236.66.33'],
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
});
