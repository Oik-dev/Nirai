import { ipcMain, shell } from 'electron'
import { stat } from 'node:fs/promises'
import { resolveAgentWorkspaceFilePath } from '../paths'

const OPEN_CHANNEL = 'external:open'
const OPEN_AGENT_FILE_CHANNEL = 'agent:open-file'

export function registerExternalIpc(): void {
  ipcMain.handle(OPEN_CHANNEL, async (_event, rawUrl: string) => {
    if (typeof rawUrl !== 'string' || !rawUrl.trim()) {
      throw new TypeError('External URL must be a non-empty string')
    }
    const url = new URL(rawUrl)
    if (url.protocol !== 'https:' && url.protocol !== 'http:') {
      throw new Error('Only http/https URLs may be opened externally')
    }
    await shell.openExternal(url.toString())
  })

  ipcMain.handle(
    OPEN_AGENT_FILE_CHANNEL,
    async (_event, rawPath: string, rawWorkingDir: string) => {
      const resolved = resolveAgentWorkspaceFilePath(rawPath, rawWorkingDir)
      const info = await stat(resolved)
      if (!info.isFile()) {
        throw new Error('Agent file reference must point to an existing file')
      }
      const openError = await shell.openPath(resolved)
      if (openError) throw new Error(openError)
    }
  )
}
