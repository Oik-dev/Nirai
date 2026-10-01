import { ipcMain } from 'electron/main';

export function installConversationProviderIpc({ isTrustedRenderer, request, isAvailable }) {
  ipcMain.handle('nirai:conversation-provider-refresh', async (event, providerId) => {
    if (!isTrustedRenderer(event)) throw new Error('untrusted renderer');
    if (!isAvailable()) throw new Error('Hubに接続されていません。');
    if (typeof providerId !== 'string' || !providerId || providerId.length > 128 || providerId.includes('\0')) {
      throw new Error('AIの接続先を選択してください。');
    }
    return request('conversation-provider-refresh', { provider_id: providerId });
  });
}
