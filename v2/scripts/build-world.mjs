import { build } from 'esbuild';

await build({
  entryPoints: ['src/renderer/world/index.js'],
  outfile: 'out/world.js',
  bundle: true,
  format: 'esm',
  platform: 'browser',
  target: 'chrome142',
  sourcemap: true,
  legalComments: 'linked',
});
