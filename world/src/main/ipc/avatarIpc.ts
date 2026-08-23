import { readFile } from 'node:fs/promises'
import { relative } from 'node:path'
import { dialog, ipcMain } from 'electron'
import { getAvatarsRoot, resolveAvatarPath } from '../paths'

const PICK_CHANNEL = 'avatar:pick'
const READ_CHANNEL = 'avatar:read'

export function registerAvatarIpc(): void {
  ipcMain.handle(PICK_CHANNEL, async () => {
    const avatarsRoot = getAvatarsRoot()
    const result = await dialog.showOpenDialog({
      defaultPath: avatarsRoot,
      properties: ['openFile'],
      filters: [{ name: 'VRM', extensions: ['vrm'] }]
    })

    if (result.canceled || result.filePaths.length === 0) {
      return null
    }

    const selectedPath = result.filePaths[0]
    const selectedRelativePath = relative(avatarsRoot, selectedPath)
    resolveAvatarPath(selectedRelativePath)
    return selectedRelativePath
  })

  ipcMain.handle(READ_CHANNEL, async (_event, relativePath: string) => {
    if (typeof relativePath !== 'string') {
      throw new TypeError('Avatar path must be a string')
    }

    const bytes = await readFile(resolveAvatarPath(relativePath))
    return new Uint8Array(bytes)
  })
}
