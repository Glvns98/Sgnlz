import { defineConfig } from 'vite';
import path from 'node:path';
import { builtinModules } from 'node:module';

export default defineConfig({
  ssr: {
    noExternal: true,
  },
  build: {
    ssr: path.resolve(__dirname, 'src/server/index.ts'),
    outDir: path.resolve(__dirname, 'dist/server'),
    emptyOutDir: true,
    target: 'node22',
    sourcemap: false,
    rollupOptions: {
      external: [
        ...builtinModules,
        ...builtinModules.map((m) => `node:${m}`),
      ],
      output: {
        format: 'cjs',
        entryFileNames: 'index.cjs',
        inlineDynamicImports: true,
      },
    },
  },
});
