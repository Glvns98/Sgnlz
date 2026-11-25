import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  root: path.resolve(__dirname, 'src/client'),
  base: './',
  build: {
    outDir: path.resolve(__dirname, 'dist/client'),
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      input: {
        index: path.resolve(__dirname, 'src/client/index.html'),
        'signal-detail': path.resolve(__dirname, 'src/client/signal-detail.html'),
        'signal-calc': path.resolve(__dirname, 'src/client/signal-calc.html'),
      },
    },
  },
});
