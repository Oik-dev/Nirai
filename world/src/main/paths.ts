import { existsSync } from 'node:fs'
import { extname, isAbsolute, join, relative, resolve, sep } from 'node:path'

function requireRootLayout(root: string): string {
  if (!existsSync(join(root, 'avatars')) || !existsSync(join(root, 'Docs'))) {
    throw new Error('NIRAI_ROOT must contain both avatars and Docs directories')
  }

  return root
}

export function getNiraiRoot(): string {
  const configuredRoot = process.env.NIRAI_ROOT?.trim()

  if (configuredRoot) {
    return requireRootLayout(resolve(configuredRoot))
  }

  return requireRootLayout(resolve(__dirname, '../../..'))
}

export function getAvatarsRoot(): string {
  return join(getNiraiRoot(), 'avatars')
}

export function getResidentsRoot(): string {
  return join(getNiraiRoot(), 'residents')
}

function isWithin(candidate: string, parent: string): boolean {
  const fromParent = relative(parent, candidate)
  return fromParent === '' || (
    fromParent !== '..'
    && !fromParent.startsWith(`..${sep}`)
    && !isAbsolute(fromParent)
  )
}

export function resolveAgentWorkspaceFilePath(rawPath: string, rawWorkingDir: string): string {
  if (typeof rawPath !== 'string' || !rawPath.trim()) {
    throw new Error('Agent file path must be a non-empty string')
  }
  if (typeof rawWorkingDir !== 'string' || !rawWorkingDir.trim() || !isAbsolute(rawWorkingDir)) {
    throw new Error('Agent working directory must be absolute')
  }

  const workspaceRoot = resolve(getNiraiRoot(), 'runtime', 'workspace')
  const workingDir = resolve(rawWorkingDir)
  if (!isWithin(workingDir, workspaceRoot)) {
    throw new Error('Agent working directory is outside the Nirai task workspace')
  }

  const candidate = isAbsolute(rawPath)
    ? resolve(rawPath)
    : resolve(workingDir, rawPath)
  if (!isWithin(candidate, workingDir)) {
    throw new Error('Agent file path escaped the active task working directory')
  }
  return candidate
}

export function resolveAvatarPath(relativePath: string): string {
  if (!relativePath || isAbsolute(relativePath)) {
    throw new Error('Avatar path must be relative to the avatars root')
  }

  if (extname(relativePath).toLowerCase() !== '.vrm') {
    throw new Error('Avatar path must use the .vrm extension')
  }

  const avatarsRoot = getAvatarsRoot()
  const candidate = resolve(avatarsRoot, relativePath)
  const fromRoot = relative(avatarsRoot, candidate)

  if (fromRoot === '..' || fromRoot.startsWith(`..${sep}`) || isAbsolute(fromRoot)) {
    throw new Error('Avatar path must stay inside the avatars root')
  }

  return candidate
}

export function resolvePersonaPath(residentName: string): string {
  const normalizedName = residentName.trim()

  if (
    !normalizedName ||
    normalizedName === '.' ||
    normalizedName === '..' ||
    /[<>:"/\\|?*\u0000-\u001f]/u.test(normalizedName)
  ) {
    throw new Error('Resident name cannot be used as a local directory name')
  }

  return join(getResidentsRoot(), normalizedName, 'persona.md')
}
