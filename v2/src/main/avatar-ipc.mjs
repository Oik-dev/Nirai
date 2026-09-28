import { dialog, ipcMain } from 'electron/main';
import { createHash, randomUUID } from 'node:crypto';
import { readAvatar } from './avatar-files.mjs';

export function installAvatarIpc({ isTrustedRenderer, request, getWindow, isAvailable }) {
  let choosing = false;
  const bindings = new Map();
  const reset = async residentId => {
    if (residentId) bindings.delete(residentId);
    else bindings.clear();
    await request('avatar-reset', residentId ? { resident_id: residentId } : {});
  };
  const residentSnapshot = async (event, id) => {
    if (!isTrustedRenderer(event)) throw new Error('untrusted renderer');
    const snapshot = await request('snapshot');
    if (typeof id !== 'string' || !snapshot.residents.some(resident => resident.id === id)) {
      throw new Error('Residentが見つかりません。');
    }
    return snapshot;
  };
  ipcMain.handle('nirai:avatar-read', async (event, id) => {
    const snapshot = await residentSnapshot(event, id);
    const path = snapshot.settings.value.resident_avatars[id];
    await reset(id);
    if (!path) return null;
    const binding = { token: randomUUID(), model_path: path, model_id: null };
    bindings.set(id, binding);
    const { bytes } = await readAvatar(path);
    const current = await residentSnapshot(event, id);
    if (bindings.get(id) !== binding || current.settings.value.resident_avatars[id] !== path) {
      throw new Error('モデルの選択が変更されました。');
    }
    binding.model_id = createHash('sha256').update(bytes).digest('hex');
    return { bytes, token: binding.token, model_id: binding.model_id };
  });
  ipcMain.handle('nirai:avatar-report', async (event, id, token, report) => {
    if (!isTrustedRenderer(event)) throw new Error('untrusted renderer');
    if (!isAvailable()) return { accepted: false, reason: 'hub_unavailable' };
    try {
      const snapshot = await residentSnapshot(event, id);
      const binding = bindings.get(id);
      if (!binding || binding.token !== token || !binding.model_id
        || snapshot.settings.value.resident_avatars[id] !== binding.model_path) {
        return { accepted: false, reason: 'stale_avatar' };
      }
      if (!report || typeof report !== 'object' || Array.isArray(report)
        || JSON.stringify(report).length > 64 * 1024) throw new Error('表示報告が不正です。');
      return await request('avatar-report', {
        resident_id: id, ...binding, capabilities: report.capabilities,
        status: report.status, applied_revision: report.applied_revision,
        ...(typeof report.error === 'string' ? { error: report.error.slice(0, 500) } : {}),
      });
    } catch (error) {
      if (!isAvailable() || error?.code === 'unavailable') return { accepted: false, reason: 'hub_unavailable' };
      throw error;
    }
  });
  ipcMain.handle('nirai:avatar-select', async (event, id, clear = false) => {
    await residentSnapshot(event, id);
    if (choosing) throw new Error('モデルを選択中です。');
    choosing = true;
    try {
      let path = null;
      if (!clear) {
        const result = await dialog.showOpenDialog(getWindow(), {
          title: 'ResidentのVRMを選択', filters: [{ name: 'VRM Avatar', extensions: ['vrm'] }],
          properties: ['openFile'],
        });
        if (result.canceled || !result.filePaths[0]) return { cancelled: true };
        path = (await readAvatar(result.filePaths[0])).path;
      }
      const snapshot = await residentSnapshot(event, id);
      const avatars = { ...snapshot.settings.value.resident_avatars };
      if (path) avatars[id] = path;
      else delete avatars[id];
      await request('command', { envelope: {
        protocol_version: 1, command_id: randomUUID(), issued_at: new Date().toISOString(),
        type: 'UpdateSettings', target: null, expected_revision: snapshot.settings.revision,
        payload: { settings: { resident_avatars: avatars } },
      } });
      return { cancelled: false };
    } finally { choosing = false; }
  });
  return { reset: () => reset().catch(() => {}) };
}
