import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  console.log('🧩 Vite mode:', mode)
  console.log('🌐 API Base URL:', env.VITE_API_BASE)

  return {
    base: '/medbot/admin/',            // 👈 ВАЖНО: префикс, где живёт SPA
    plugins: [react()],
    server: { host: '0.0.0.0', port: 5173 },
    build: { outDir: 'dist' },
  }
})
