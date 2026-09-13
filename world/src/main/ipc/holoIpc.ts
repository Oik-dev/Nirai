import { ipcMain } from 'electron'
import type { HoloAddonHost } from '../holo/HoloWebHost'
import { isHoloAutoResumeTrigger, type HoloSurfaceBounds } from '../holo/holoWeb'

interface HoloSurfaceRequest {
  readonly visible: boolean
  readonly bounds?: HoloSurfaceBounds
}

function requireHost(getHost: () => HoloAddonHost | null): HoloAddonHost {
  const host = getHost()
  if (!host) throw new Error('Holo Addon host is not available')
  return host
}

function isFiniteBounds(value: unknown): value is HoloSurfaceBounds {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<HoloSurfaceBounds>
  return [candidate.x, candidate.y, candidate.width, candidate.height]
    .every((item) => typeof item === 'number' && Number.isFinite(item))
}

export function registerHoloIpc(getHost: () => HoloAddonHost | null): void {
  ipcMain.handle('holo:surface', async (_event, request: HoloSurfaceRequest) => {
    if (!request || typeof request.visible !== 'boolean') {
      throw new Error('Invalid Holo surface request')
    }
    if (request.visible && !isFiniteBounds(request.bounds)) {
      throw new Error('Visible Holo surface requires finite bounds')
    }
    return requireHost(getHost).setSurface(request.visible, request.bounds)
  })

  ipcMain.handle('holo:status', () => requireHost(getHost).getStatus())
  ipcMain.handle('holo:task-management', () => requireHost(getHost).taskManagementState())
  ipcMain.handle('holo:cancel-auto-resume', (_event, taskId: unknown) => {
    if (typeof taskId !== 'string') throw new Error('Invalid Task ID')
    return requireHost(getHost).cancelAutoResume(taskId)
  })
  ipcMain.handle('holo:auto-resume', async (_event, trigger: unknown) => {
    if (!isHoloAutoResumeTrigger(trigger)) throw new Error('Invalid Holo auto-resume trigger')
    return requireHost(getHost).enqueueAutoResume(trigger)
  })
  ipcMain.handle('holo:prepare-dive', () => requireHost(getHost).prepareDive())
  ipcMain.handle('holo:reload', () => requireHost(getHost).reload())
  ipcMain.handle('holo:skin-fallback-qa', () => requireHost(getHost).simulateSkinFallbackForQa())
}
