import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { HubStore } from '../src/hub/store.js';

test('avatar selection survives Hub restart, rejects unknown residents and respects settings revision', () => {
  const root = mkdtempSync(join(tmpdir(), 'nirai-world-settings-'));
  const path = join(root, 'hub.sqlite3');
  let store = new HubStore(path);
  try {
    store.ensureResident('holo', 'Holo');
    const avatar = resolve(root, 'lapan.vrm');
    const revision = store.getSettings().revision;
    store.updateSettings({ resident_avatars: { holo: avatar } }, revision);
    assert.throws(() => store.updateSettings({ resident_avatars: {} }, revision), /stale/);
    store.close(); store = new HubStore(path);
    assert.equal(store.getSettings().value.resident_avatars.holo, avatar);
    const current = store.getSettings().revision;
    for (const resident_avatars of [{ stranger: avatar }, { holo: '../model.vrm' }, { holo: resolve(root, 'x.txt') }, []]) {
      assert.throws(() => store.updateSettings({ resident_avatars }, current));
    }
    store.updateSettings({ resident_avatars: {} }, current);
    assert.deepEqual(store.getSettings().value.resident_avatars, {});
  } finally { store.close(); rmSync(root, { recursive: true, force: true }); }
});
