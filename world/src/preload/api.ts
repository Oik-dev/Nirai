import { ipcRenderer } from 'electron'

export interface NiraiApi {
  avatar: {
    pick(): Promise<string | null>
    read(relativePath: string): Promise<Uint8Array>
  }
}

export const niraiApi: NiraiApi = Object.freeze({
  avatar: Object.freeze({
    pick: () => ipcRenderer.invoke('avatar:pick'),
    read: (relativePath: string) => ipcRenderer.invoke('avatar:read', relativePath)
  })
})
