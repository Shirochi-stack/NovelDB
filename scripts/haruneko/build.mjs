// Bundles HaruNeko's website plugins with NovelDB's platform layer into one browser script.
//   node build.mjs <haruneko checkout> <output file>
// Swapped modules: platform/FetchProvider (src/platform.ts), BackgroundTimers (src/timers.ts),
// RemoteBrowserWindow and AntiScrapingDetection (src/remote-window.ts). Left out: the image
// descrambler (src/withheld.ts) and the DRM decryption scripts (*.DRM.js, replaced by functions that
// throw). Icons are dropped; .proto schemas are inlined as text.
import { build } from 'esbuild';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const [checkout, outfile] = process.argv.slice(2);
if (!checkout || !outfile) {
    console.error('usage: node build.mjs <haruneko checkout> <output file>');
    process.exit(2);
}
const web = path.resolve(checkout, 'web', 'src');
const own = (name) => path.join(here, 'src', name);
const swaps = [
    [/[\\/]platform[\\/]FetchProvider(\.ts)?$/, own('platform.ts')],
    [/[\\/]BackgroundTimers(\.ts)?$/, own('timers.ts')],
    [/[\\/]platform[\\/](RemoteBrowserWindow|AntiScrapingDetection)(\.ts|\.js)?$/, own('remote-window.ts')],
    [/[\\/]transformers[\\/]ImageDescrambler(\.ts)?$/, own('withheld.ts')],
];

const noveldb = {
    name: 'noveldb-haruneko',
    setup(b) {
        b.onResolve({ filter: /.*/ }, (args) => {
            if (!args.importer || args.namespace !== 'file') return undefined;
            if (/\.(webp|png|jpe?g|svg|gif|ico)$/i.test(args.path)) {
                return { path: args.path, namespace: 'icon' };
            }
            if (/\.DRM(\.js)?$/.test(args.path)) {
                const file = path.resolve(path.dirname(args.importer), args.path);
                return { path: file.endsWith('.js') ? file : file + '.js', namespace: 'withheld' };
            }
            if (args.path.endsWith('?raw')) {  // Vite: the file's text as a string
                return { path: path.resolve(path.dirname(args.importer), args.path.slice(0, -4)), namespace: 'raw' };
            }
            if (!args.path.startsWith('.')) return undefined;
            const target = path.resolve(path.dirname(args.importer), args.path);
            for (const [pattern, replacement] of swaps) {
                if (pattern.test(target) && args.importer !== replacement) return { path: replacement };
            }
            return undefined;
        });
        b.onLoad({ filter: /.*/, namespace: 'icon' }, () => ({ contents: 'export default "";', loader: 'js' }));
        b.onLoad({ filter: /.*/, namespace: 'raw' }, (args) => ({ contents: readFileSync(args.path, 'utf8'), loader: 'text' }));
        // A DRM script's exports become functions that throw: the website loads, reading it stops.
        b.onLoad({ filter: /.*/, namespace: 'withheld' }, (args) => {
            const source = readFileSync(args.path, 'utf8');
            const names = new Set();
            for (const match of source.matchAll(/export\s+(?:async\s+)?(?:function\*?|const|let|var|class)\s+([A-Za-z_$][\w$]*)/g)) names.add(match[1]);
            for (const match of source.matchAll(/export\s*\{([^}]*)\}/g)) {
                for (const part of match[1].split(',')) {
                    const name = part.trim().split(/\s+as\s+/).pop();
                    if (name) names.add(name);
                }
            }
            const hasDefault = /export\s+default/.test(source);
            const body = [`import { WITHHELD } from ${JSON.stringify(own('withheld.ts'))};`,
                'function withheld() { throw new Error(WITHHELD); }'];
            for (const name of names) if (name !== 'default') body.push(`export const ${name} = withheld;`);
            if (hasDefault) body.push('export default withheld;');
            return { contents: body.join('\n'), loader: 'js', resolveDir: here };
        });
        // The app writes the files itself: HaruNeko's exporters (zip, EPUB, PDF) are not needed.
        b.onLoad({ filter: /[\\/]exporters[\\/]MangaExporterRegistry\.ts$/ }, (args) => {
            const source = readFileSync(args.path, 'utf8');
            const kept = source.slice(0, source.indexOf('export function CreateChapterExportRegistry'))
                .replace(/^import \{[^}]*\} from '\.\/\w+Exporter';\s*$/gm, '');
            return {
                contents: kept + '\nexport function CreateChapterExportRegistry(): never { throw new Error("not used in NovelDB"); }\n',
                loader: 'ts',
            };
        });
        // No HaruNeko user interface either.
        b.onLoad({ filter: /[\\/]frontend[\\/]classic[\\/]FrontendInfo\.ts$/ }, (args) => ({
            contents: readFileSync(args.path, 'utf8').replace(/\(await import\('\.\/FrontendClassic'\)\)\.default/,
                'Promise.reject(new Error("no HaruNeko interface in NovelDB"))'),
            loader: 'ts',
        }));
        // HaruNeko's concealed Request extends the browser's own Request, never the injected one.
        b.onLoad({ filter: /FetchConcealedRequest\.ts$/ }, (args) => ({
            contents: readFileSync(args.path, 'utf8').replace(/extends Request\b/, 'extends globalThis.Request'),
            loader: 'ts',
        }));
    },
};

await build({
    entryPoints: [own('entry.ts')],
    outfile,
    bundle: true,
    format: 'iife',
    platform: 'browser',
    target: 'es2022',
    minify: true,
    legalComments: 'none',
    alias: { '@hn': web },
    inject: [own('request.ts')],
    loader: { '.proto': 'text' },
    tsconfigRaw: { compilerOptions: { experimentalDecorators: true, useDefineForClassFields: false } },
    plugins: [noveldb],
    logLevel: 'warning',
    nodePaths: [path.join(here, 'node_modules')],
});
