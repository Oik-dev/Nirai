import { existsSync, readFileSync, realpathSync } from 'node:fs'
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

export function resolveAgentWorkspaceFilePath(rawPath: string, rawAgentSessionId: string): string {
  if (typeof rawPath !== 'string' || !rawPath.trim()) {
    throw new Error('Agent file path must be a non-empty string')
  }
  const agentSessionId = rawAgentSessionId?.trim()
  if (!agentSessionId || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(agentSessionId)) {
    throw new Error('Agent session id is invalid')
  }

  const niraiRoot = getNiraiRoot()
  const sessionRoot = resolve(niraiRoot, 'runtime', 'agent_sessions')
  const sessionDir = resolve(sessionRoot, agentSessionId)
  if (!isWithin(sessionDir, sessionRoot)) {
    throw new Error('Agent session escaped the runtime session root')
  }

  let snapshot: unknown
  try {
    snapshot = JSON.parse(readFileSync(join(sessionDir, 'session.json'), 'utf8'))
  } catch {
    throw new Error('Agent session snapshot could not be read')
  }
  if (typeof snapshot !== 'object' || snapshot === null || Array.isArray(snapshot)) {
    throw new Error('Agent session snapshot is invalid')
  }
  const rawWorkingDir = (snapshot as Record<string, unknown>).working_dir
  if (typeof rawWorkingDir !== 'string' || !rawWorkingDir.trim() || !isAbsolute(rawWorkingDir)) {
    throw new Error('Agent session working directory is invalid')
  }
  const configuredWorkingDir = resolve(rawWorkingDir)
  let workingDir: string
  try {
    workingDir = realpathSync(configuredWorkingDir)
  } catch {
    throw new Error('Agent session working directory no longer exists')
  }

  const lexicalCandidate = isAbsolute(rawPath)
    ? resolve(rawPath)
    : resolve(configuredWorkingDir, rawPath)
  if (!isWithin(lexicalCandidate, configuredWorkingDir)) {
    throw new Error('Agent file path escaped the authorized task working directory')
  }
  let candidate: string
  try {
    candidate = realpathSync(lexicalCandidate)
  } catch {
    throw new Error('Agent file reference does not exist')
  }
  if (!isWithin(candidate, workingDir)) {
    throw new Error('Agent file path escaped the authorized task working directory through a link')
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
