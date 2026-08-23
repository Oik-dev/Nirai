import { resolve } from 'node:path'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: resolve('src/main/main.ts'),
        output: {
          entryFileNames: 'index.js'
        }
      }
    }
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: resolve('src/preload/index.ts'),
        output: {
          entryFileNames: 'index.js'
        }
      }
    }
  },
  renderer: {
    root: resolve('src/renderer'),
    publicDir: resolve('public'),
    plugins: [react()],
    resolve: {
      dedupe: ['three']
    }
  }
})
