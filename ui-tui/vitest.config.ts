import { defineConfig } from 'vitest/config'
import { fileURLToPath } from 'node:url'

const hermesInkSource = fileURLToPath(new URL('./packages/hermes-ink/src/entry-exports.ts', import.meta.url))

export default defineConfig({
  resolve: {
    alias: {
      '@hermes/ink': hermesInkSource
    }
  },
  test: {
    exclude: ['dist/**', 'node_modules/**']
  }
})
