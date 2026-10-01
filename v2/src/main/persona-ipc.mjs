import { dialog, ipcMain } from 'electron/main';
import { randomUUID } from 'node:crypto';
import { readPersona } from '../../out/src/hub/persona.js';

export function installPersonaIpc({ isTrustedRenderer, request, getWindow, isAvailable }) {
  let choosing = false;
  ipcMain.handle('nirai:persona-select', async (event, residentId, clear = false) => {
    if (!isTrustedRenderer(event)) throw new Error('untrusted renderer');
    if (!isAvailable()) throw new Error('Hubに接続されていません。');
    const before = await request('snapshot');
    if (typeof residentId !== 'string' || !before.residents.some(item => item.id === residentId)) {
      throw new Error('Residentが見つかりません。');
    }
    if (choosing) throw new Error('Personaを選択中です。');
    choosing = true;
    try {
      let path = null;
      if (!clear) {
        const selected = await dialog.showOpenDialog(getWindow(), {
          title: 'ResidentのPersonaを選択',
          filters: [{ name: 'Persona', extensions: ['md', 'txt'] }],
          properties: ['openFile'],
        });
        if (selected.canceled || !selected.filePaths[0]) return { cancelled: true };
        path = (await readPersona(selected.filePaths[0])).path;
      }
      await request('command', { envelope: {
        protocol_version: 1, command_id: randomUUID(), issued_at: new Date().toISOString(),
        type: 'UpdateResident', target: null,
        payload: { resident_id: residentId, persona_path: path },
      } });
      return { cancelled: false };
    } finally { choosing = false; }
  });
}
