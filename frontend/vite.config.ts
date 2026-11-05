import { createHmac } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv, type Plugin, type ProxyOptions } from 'vite'

// Local development gateway: credentials stay in the Node process, never in VITE_*.
export default defineConfig(({ mode }) => {
  const backend = process.env.RESEARCH_CONFIG_DIR || fileURLToPath(new URL('../backend', import.meta.url))
  const apiTarget = process.env.RESEARCH_API_TARGET || 'http://127.0.0.1:8001'
  const env = loadEnv(mode, backend, '')
  const researchEnv = loadEnv('research', backend, '')
  const secret = process.env.JWT_SECRET_KEY || researchEnv.JWT_SECRET_KEY || env.JWT_SECRET_KEY
  function token() {
    if (!secret || secret.length < 32) throw new Error('Configure backend/.env.research JWT_SECRET_KEY first')
    const encode = (value: unknown) => Buffer.from(JSON.stringify(value)).toString('base64url')
    const now = Math.floor(Date.now() / 1000)
    const data = `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode({ sub: 'local-researcher', iat: now, exp: now + 300 })}`
    return `${data}.${createHmac('sha256', secret).update(data).digest('base64url')}`
  }
  const apiProxy: ProxyOptions = {
    target: apiTarget,
    configure(proxy) {
      proxy.on('proxyReq', (request) => request.setHeader('Authorization', `Bearer ${token()}`))
    },
  }
  const localGuard: Plugin = {
    name: 'research-local-gateway',
    configureServer(server) { guard(server) },
    configurePreviewServer(server) { guard(server) },
  }
  function guard(server: { middlewares: { use: (handler: (req: import('node:http').IncomingMessage, res: import('node:http').ServerResponse, next: () => void) => void) => unknown } }) {
    server.middlewares.use((req, res, next) => {
      if (req.url?.startsWith('/api/v1') || req.url?.startsWith('/research-health')) {
        const origin = req.headers.origin
        const ownOrigin = req.headers.host && `http://${req.headers.host}`
        if ((origin && origin !== ownOrigin) || req.headers['sec-fetch-site'] === 'cross-site') {
          res.statusCode = 403
          res.end('Local requests only')
          return
        }
      }
      next()
    })
  }
  const proxy = {
    '/api/v1': apiProxy,
    '/research-health': { target: apiTarget, rewrite: () => '/health' },
  }
  return {
    plugins: [react(), localGuard],
    server: { host: '127.0.0.1', port: 5183, strictPort: true, cors: false, proxy },
    preview: { host: '127.0.0.1', port: 5184, strictPort: true, cors: false, proxy },
    resolve: { alias: [{ find: /^@\//, replacement: '/src/' }] },
  }
})
