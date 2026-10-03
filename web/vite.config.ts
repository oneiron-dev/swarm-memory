import { defineConfig, type Plugin } from 'vite'
import preact from '@preact/preset-vite'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))

// `vite --mode sample` serves ./data/* from the synthetic fixture bundle in sample-data/.
function sampleData(dir: string): Plugin {
  return {
    name: 'sample-data',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = (req.url || '').split('?')[0]
        if (!url.startsWith('/data/')) return next()
        const file = path.resolve(dir, decodeURIComponent(url.slice('/data/'.length)))
        if (!file.startsWith(dir + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
          res.statusCode = 404
          res.end('not found')
          return
        }
        res.setHeader('Content-Type', 'application/json')
        fs.createReadStream(file).pipe(res)
      })
    },
  }
}

export default defineConfig(({ mode }) => ({
  base: './',
  plugins: [preact(), ...(mode === 'sample' ? [sampleData(path.join(here, 'sample-data'))] : [])],
  server:
    mode === 'sample'
      ? {}
      : {
          // the real bundle stays on Arch; dev proxies to it instead of copying it here
          proxy: {
            '/data': {
              target: process.env.DATA_ORIGIN || 'https://arch.tail87c1d7.ts.net',
              changeOrigin: true,
              secure: true,
            },
          },
        },
  build: { target: 'es2022', assetsInlineLimit: 0 },
}))
