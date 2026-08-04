import * as esbuild from 'esbuild';

const watch = process.argv.includes('--watch');
const context = await esbuild.context({
    entryPoints: { index: 'src/index.js', style: 'src/ui/style.css' },
    outdir: 'dist',
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
