import { resolve } from 'node:path';
import { runElectronSmoke } from './electron-smoke.mjs';

await runElectronSmoke({
  name: 'holo',
  appPath: resolve(import.meta.dirname, '../holo-smoke/main.mjs'),
  timeoutMs: 45_000,
  rootVariable: 'NIRAI_HOLO_SMOKE_ROOT',
});
