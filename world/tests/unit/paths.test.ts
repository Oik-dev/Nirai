import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { mkdtemp, mkdir, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { resolveAvatarPath } from '../../src/main/paths'

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
})
