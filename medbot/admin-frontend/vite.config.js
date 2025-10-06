import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// Загружаем переменные окружения в зависимости от режима (dev / prod)
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  console.log('🧩 Vite mode:', mode)
  console.log('🌐 API Base URL:', env.VITE_API_BASE)

  return {
    plugins: [react()],
    server: {
      host: '0.0.0.0',
      port: 5173
    },
    build: {
      outDir: 'dist',
    }
  }
})
