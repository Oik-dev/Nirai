import { describe, expect, it } from 'vitest'
import { VrmLoader } from '../../src/renderer/src/world/vrm/VrmLoader'

describe('VrmLoader', () => {
  it('rejects invalid binary data without producing a VRM', async () => {
    const loader = new VrmLoader()
    const invalidBytes = new TextEncoder().encode('not-a-vrm')

    await expect(loader.load(invalidBytes)).rejects.toThrow()
  })
})
