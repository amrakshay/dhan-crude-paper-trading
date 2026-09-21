import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The API and the app's own WebSocket are proxied so the browser sees a single
// origin in dev. That keeps the session cookie same-origin, which matters for
// the WebSocket handshake (browsers will not attach cross-origin cookies to it
// reliably).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:24601', changeOrigin: false },
      '/ws': { target: 'ws://127.0.0.1:24601', ws: true, changeOrigin: false },
    },
  },
  build: { outDir: 'dist', sourcemap: true },
});
