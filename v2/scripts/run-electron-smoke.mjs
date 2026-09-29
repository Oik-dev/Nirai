import { resolve } from 'node:path';
import { runElectronSmoke } from './electron-smoke.mjs';

await runElectronSmoke({
  name: 'main',
  appPath: resolve(import.meta.dirname, '../smoke'),
  timeoutMs: 15_000,
  environment: { NIRAI_V2_UI_SMOKE: '', NIRAI_V2_SMOKE: '1' },
});
