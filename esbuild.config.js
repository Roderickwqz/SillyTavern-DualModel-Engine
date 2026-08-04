import * as esbuild from 'esbuild';
import { rm } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const watch = process.argv.includes('--watch');
const outdir = join(dirname(fileURLToPath(import.meta.url)), 'dist');

if (!watch) {
    await rm(outdir, { recursive: true, force: true });
}

const context = await esbuild.context({
    entryPoints: { index: 'src/index.js', style: 'src/ui/style.css' },
    outdir,
    bundle: true,
    splitting: true,
    format: 'esm',
    target: 'es2022',
    sourcemap: false,
    external: ['/script.js', '/scripts/*'],
    loader: { '.html': 'text', '.json': 'json' },
});

if (watch) {
    await context.watch();
} else {
    await context.rebuild();
    await context.dispose();
}
