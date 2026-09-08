import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { mkdtemp, mkdir, rm, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { resolveAgentWorkspaceFilePath, resolveAvatarPath } from '../../src/main/paths'

describe('resolveAvatarPath', () => {
  let niraiRoot: string
  let previousRoot: string | undefined

  beforeEach(async () => {
    previousRoot = process.env.NIRAI_ROOT
    niraiRoot = await mkdtemp(join(tmpdir(), 'nirai-paths-'))
    await mkdir(join(niraiRoot, 'avatars', 'resident-a'), { recursive: true })
    await mkdir(join(niraiRoot, 'Docs'), { recursive: true })
    process.env.NIRAI_ROOT = niraiRoot
  })

  afterEach(async () => {
    if (previousRoot === undefined) {
      delete process.env.NIRAI_ROOT
    } else {
      process.env.NIRAI_ROOT = previousRoot
    }
    await rm(niraiRoot, { recursive: true, force: true })
  })

  it('accepts a nested VRM path inside the avatars root', () => {
    expect(resolveAvatarPath(join('resident-a', 'avatar.vrm'))).toBe(
      resolve(niraiRoot, 'avatars', 'resident-a', 'avatar.vrm')
    )
  })

  it('rejects traversal outside the avatars root', () => {
    expect(() => resolveAvatarPath(join('..', 'outside.vrm'))).toThrow(/avatars root/i)
  })

  it('rejects a non-VRM extension', () => {
    expect(() => resolveAvatarPath('avatar.glb')).toThrow(/\.vrm/i)
  })

  it('opens Agent file references only inside the Core-authorized session working directory', async () => {
    const workingDir = resolve(niraiRoot, 'runtime', 'workspace', 'TASK-1')
    const sessionDir = resolve(niraiRoot, 'runtime', 'agent_sessions', 'AS-1')
    await mkdir(workingDir, { recursive: true })
    await mkdir(sessionDir, { recursive: true })
    await writeFile(resolve(workingDir, 'result.txt'), 'ok', 'utf8')
    await writeFile(
      resolve(sessionDir, 'session.json'),
      JSON.stringify({ working_dir: workingDir }),
      'utf8'
    )
    expect(resolveAgentWorkspaceFilePath('result.txt', 'AS-1')).toBe(
      resolve(workingDir, 'result.txt')
    )
    expect(() => resolveAgentWorkspaceFilePath('..\\escape.txt', 'AS-1')).toThrow(/escaped/i)
    expect(() => resolveAgentWorkspaceFilePath(
      resolve(niraiRoot, 'Docs', 'secret.txt'),
      'AS-1'
    )).toThrow(/escaped/i)
  })

  it('allows artifacts in an external project only through its durable Agent session snapshot', async () => {
    const externalDir = await mkdtemp(join(tmpdir(), 'nirai-external-project-'))
    const sessionDir = resolve(niraiRoot, 'runtime', 'agent_sessions', 'AS-EXTERNAL')
    try {
      await mkdir(sessionDir, { recursive: true })
      await writeFile(
        resolve(sessionDir, 'session.json'),
        JSON.stringify({ working_dir: externalDir }),
        'utf8'
      )
      await writeFile(resolve(externalDir, 'artifact.txt'), 'artifact', 'utf8')
      expect(resolveAgentWorkspaceFilePath('artifact.txt', 'AS-EXTERNAL')).toBe(
        resolve(externalDir, 'artifact.txt')
      )
      expect(() => resolveAgentWorkspaceFilePath(
        resolve(niraiRoot, 'Docs', 'secret.txt'),
        'AS-EXTERNAL'
      )).toThrow(/escaped/i)
    } finally {
      await rm(externalDir, { recursive: true, force: true })
    }
  })

  it('rejects Agent file references that escape through a junction', async () => {
    const workingDir = resolve(niraiRoot, 'runtime', 'workspace', 'TASK-LINK')
    const sessionDir = resolve(niraiRoot, 'runtime', 'agent_sessions', 'AS-LINK')
    const outsideDir = await mkdtemp(join(tmpdir(), 'nirai-outside-link-'))
    try {
      await mkdir(workingDir, { recursive: true })
      await mkdir(sessionDir, { recursive: true })
      await writeFile(resolve(outsideDir, 'secret.txt'), 'outside', 'utf8')
      await symlink(outsideDir, resolve(workingDir, 'linked'), 'junction')
      await writeFile(
        resolve(sessionDir, 'session.json'),
        JSON.stringify({ working_dir: workingDir }),
        'utf8'
      )
      expect(() => resolveAgentWorkspaceFilePath('linked\\secret.txt', 'AS-LINK'))
        .toThrow(/through a link/i)
    } finally {
      await rm(outsideDir, { recursive: true, force: true })
    }
  })
})
