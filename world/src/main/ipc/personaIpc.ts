import { access } from 'node:fs/promises'
import { shell, ipcMain } from 'electron'
import { resolvePersonaPath } from '../paths'

const OPEN_CHANNEL = 'persona:open'

export function registerPersonaIpc(): void {
  ipcMain.handle(OPEN_CHANNEL, async (_event, residentName: string) => {
    if (typeof residentName !== 'string') {
      throw new TypeError('Resident name must be a string')
    }

    const personaPath = resolvePersonaPath(residentName)
    await access(personaPath)
    const error = await shell.openPath(personaPath)
    if (error) {
      throw new Error(error)
    }
  })
}
