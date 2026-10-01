import { resolve } from 'node:path';
import { runElectronSmoke } from './electron-smoke.mjs';

await runElectronSmoke({
  name: 'holo-preparation',
  appPath: resolve(import.meta.dirname, '../holo-smoke/preparation.mjs'),
  timeoutMs: 20_000,
  rootVariable: 'NIRAI_HOLO_SMOKE_ROOT',
});
