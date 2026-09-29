import { resolve } from 'node:path';
import { runElectronSmoke } from './electron-smoke.mjs';

await runElectronSmoke({
  name: 'ui',
  appPath: resolve(import.meta.dirname, '../ui-smoke'),
  timeoutMs: process.env.NIRAI_V2_WORLD_SMOKE_APPEARANCE === '1' ? 240_000
    : process.env.NIRAI_V2_WORLD_SMOKE === '1' ? 90_000 : 45_000,
  environment: { NIRAI_V2_SMOKE: '', NIRAI_V2_UI_SMOKE: '1' },
});
