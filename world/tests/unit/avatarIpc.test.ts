import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { join } from 'node:path'

const electronMocks = vi.hoisted(() => {
  const handlers = new Map<string, (...args: unknown[]) => unknown>()

  return {
    handlers,
    handle: vi.fn((channel: string, handler: (...args: unknown[]) => unknown) => {
      handlers.set(channel, handler)
    }),
    showOpenDialog: vi.fn()
  }
})

vi.mock('electron', () => ({
  dialog: {
    showOpenDialog: electronMocks.showOpenDialog
  },
  ipcMain: {
    handle: electronMocks.handle
  }
}))

import { registerAvatarIpc } from '../../src/main/ipc/avatarIpc'

describe('registerAvatarIpc', () => {
  const niraiRoot = 'D:\\Products\\Nirai'
  let previousRoot: string | undefined

  beforeEach(() => {
    previousRoot = process.env.NIRAI_ROOT
    process.env.NIRAI_ROOT = niraiRoot
    electronMocks.handlers.clear()
    electronMocks.handle.mockClear()
    electronMocks.showOpenDialog.mockReset()
    registerAvatarIpc()
  })

  afterEach(() => {
    if (previousRoot === undefined) {
      delete process.env.NIRAI_ROOT
    } else {
      process.env.NIRAI_ROOT = previousRoot
    }
  })

  it('opens the picker at avatars and restricts selection to VRM files', async () => {
    electronMocks.showOpenDialog.mockResolvedValue({ canceled: true, filePaths: [] })
    const pick = electronMocks.handlers.get('avatar:pick')

    expect(pick).toBeDefined()
    await pick?.()

    expect(electronMocks.showOpenDialog).toHaveBeenCalledWith({
      defaultPath: join(niraiRoot, 'avatars'),
      properties: ['openFile'],
      filters: [{ name: 'VRM', extensions: ['vrm'] }]
    })
  })

  it('returns an avatars-root-relative path for a selected VRM', async () => {
    electronMocks.showOpenDialog.mockResolvedValue({
      canceled: false,
      filePaths: [join(niraiRoot, 'avatars', 'resident-a', 'avatar.vrm')]
    })
    const pick = electronMocks.handlers.get('avatar:pick')

    await expect(pick?.()).resolves.toBe(join('resident-a', 'avatar.vrm'))
  })
})
