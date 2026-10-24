// voice-ai-girlfriend build: esbuild browser bundle shaped like the DSH
// client plugin artifact (lib/client.js, __ModuleLoader__ closure). All
// @deepseek-ai / react imports stay external — the DSH host provides them.
//
// CSS modules: dsh (rc.8, 0.1.3 and the 0.2.x web plugin system alike) does NOT
// auto-load css sidecars. Every client plugin must inject its own <style
// data-plugin data-plugin-css> tags (hashed class names). This build therefore
// compiles *.module.css into (a) a local->hashed class map for the TSX and (b)
// an injected <style> per css module. A server/node entry (lib/index.js) is also
// emitted so the dsh loader can import the profile entry on every supported
// release.
import { build } from 'esbuild'
import { mkdirSync, readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { basename, dirname } from 'node:path'

const PKG_ID = '@beiyege-01/dsh-voice-ai-girlfriend'

const EXTERNALS = [
  'react',
  'react/jsx-runtime',
  'react-dom',
  'react-dom/client',
  '@deepseek-ai/cordis',
  '@deepseek-ai/dsh-client-runtime',
  '@deepseek-ai/dsh-client-ui-conversation',
  '@deepseek-ai/dsh-client-ui-chat',
  '@deepseek-ai/dsh-client-ui-slots',
  '@deepseek-ai/dsh-client-locale',
  '@deepseek-ai/dsh-invariants',
  '@deepseek-ai/dsh-client-ui-primitives',
  '@deepseek-ai/dsh-client-web-react',
]

/** dsh-compatible CSS-modules loader: hashes class names, injects <style>. */
const dshCssModules = {
  name: 'dsh-css-modules',
  setup(build) {
    build.onLoad({ filter: /\.css$/, namespace: 'file' }, async (args) => {
      const raw = readFileSync(args.path, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
      const tagId = `${PKG_ID}/${basename(args.path)}`
      const map = {}
      const css = raw.replace(/\.([_a-zA-Z][\w-]*)/g, (match, name) => {
        if (map[name] === undefined) {
          const hash = createHash('md5').update(`${args.path}:${name}`).digest('hex').slice(0, 6)
          // CSS identifiers cannot start with a digit; an md5 hex hash often
          // does (e.g. 6d13ea), which silently drops the whole rule. Ensure the
          // leading char is a letter so every scoped class is a valid selector.
          const safeHash = /^[0-9]/.test(hash) ? `a${hash}` : hash
          map[name] = `${safeHash}_${name}`
        }
        return `.${map[name]}`
      })
      const js = [
        `const css = ${JSON.stringify(css)};`,
        `const tagId = ${JSON.stringify(tagId)};`,
        `if (typeof document !== 'undefined') {`,
        `  const q = 'style[data-plugin-css=' + JSON.stringify(tagId) + ']';`,
        `  if (!document.querySelector(q)) {`,
        `    const el = document.createElement('style');`,
        `    el.dataset.plugin = ${JSON.stringify(PKG_ID)};`,
        `    el.dataset.pluginCss = tagId;`,
        `    el.textContent = css;`,
        `    document.head.appendChild(el);`,
        `  }`,
        `}`,
        `export default ${JSON.stringify(map)};`,
      ].join('\n')
      return { contents: js, loader: 'js', resolveDir: dirname(args.path) }
    })
  },
}

mkdirSync('lib', { recursive: true })

// Server/node half: a deliberate no-op (browser logic lives in client), but
// the module must EXIST so `dsh` can import the entry when composing the tree.
await build({
  entryPoints: ['src/index.ts'],
  outfile: 'lib/index.js',
  bundle: false,
  format: 'esm',
  platform: 'node',
  logLevel: 'info',
})

await build({
  entryPoints: ['src/client/index.ts'],
  outfile: 'lib/client.js',
  bundle: true,
  format: 'cjs',
  platform: 'browser',
  jsx: 'automatic',
  external: EXTERNALS,
  plugins: [dshCssModules],
  banner: {
    js:
      `window.__ModuleLoader__.load({ id: ${JSON.stringify(PKG_ID)}, factory: (require) => {\n` +
      'var module = { exports: {} }; var exports = module.exports;',
  },
  footer: {
    js: 'return module.exports; } });',
  },
  logLevel: 'info',
})

console.log('[voice-ai-girlfriend] build done: lib/index.js + lib/client.js')
