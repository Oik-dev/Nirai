import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => {
  const handlers = new Map<string, (...args: unknown[]) => unknown>()
  return {
    handlers,
    handle: vi.fn((channel: string, handler: (...args: unknown[]) => unknown) => {
      handlers.set(channel, handler)
    }),
    openPath: vi.fn(),
    access: vi.fn(),
    resolvePersonaPath: vi.fn(() => 'D:\\Products\\Nirai\\residents\\Lapan\\persona.md')
  }
})

vi.mock('electron', () => ({
  ipcMain: { handle: mocks.handle },
  shell: { openPath: mocks.openPath }
}))

vi.mock('node:fs/promises', () => ({ access: mocks.access }))
vi.mock('../../src/main/paths', () => ({ resolvePersonaPath: mocks.resolvePersonaPath }))

import { registerPersonaIpc } from '../../src/main/ipc/personaIpc'

describe('registerPersonaIpc', () => {
  beforeEach(() => {
    mocks.handlers.clear()
    mocks.handle.mockClear()
    mocks.openPath.mockReset().mockResolvedValue('')
    mocks.access.mockReset().mockResolvedValue(undefined)
    mocks.resolvePersonaPath.mockClear()
    registerPersonaIpc()
  })

  it('opens the resident persona with the Windows default application', async () => {
    const open = mocks.handlers.get('persona:open')

    await expect(open?.({}, 'Lapan')).resolves.toBeUndefined()
    expect(mocks.resolvePersonaPath).toHaveBeenCalledWith('Lapan')
    expect(mocks.access).toHaveBeenCalledWith('D:\\Products\\Nirai\\residents\\Lapan\\persona.md')
    expect(mocks.openPath).toHaveBeenCalledWith('D:\\Products\\Nirai\\residents\\Lapan\\persona.md')
  })

  it('rejects shell errors instead of pretending the prompt opened', async () => {
    mocks.openPath.mockResolvedValue('no associated application')
    const open = mocks.handlers.get('persona:open')

    await expect(open?.({}, 'Lapan')).rejects.toThrow('no associated application')
  })
})
