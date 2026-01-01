import { defineConfig } from 'vite'
import { createVuePlugin } from 'vite-plugin-vue2'

export default defineConfig({
  plugins: [createVuePlugin()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    allowedHosts: true
  }
})
