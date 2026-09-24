import fs from 'node:fs'

const filePath = process.argv[2]
if (!filePath) {
  console.error('Usage: node scripts/inspect-vrm-expressions.mjs <vrm-path>')
  process.exit(1)
}

const buffer = fs.readFileSync(filePath)
const magic = buffer.toString('utf8', 0, 4)
if (magic !== 'glTF') {
  throw new Error('Not a GLB/VRM file')
}

const jsonChunkLength = buffer.readUInt32LE(12)
const jsonChunkType = buffer.readUInt32LE(16)
if (jsonChunkType !== 0x4e4f534a) {
  throw new Error('First GLB chunk is not JSON')
}

const jsonText = buffer.toString('utf8', 20, 20 + jsonChunkLength).replace(/\u0000+$/g, '')
const gltf = JSON.parse(jsonText)

const vrm1 = gltf.extensions?.VRMC_vrm
if (vrm1) {
  const preset = vrm1.expressions?.preset ?? {}
  const custom = vrm1.expressions?.custom ?? {}
  const presetNames = Object.keys(preset)
  const customNames = Object.keys(custom)
  console.log(JSON.stringify({
    spec: 'VRM 1.0',
    total: presetNames.length + customNames.length,
    presetCount: presetNames.length,
    preset: presetNames.map((name) => ({
      name,
      overrideBlink: preset[name]?.overrideBlink ?? null
    })),
    customCount: customNames.length,
    custom: customNames.map((name) => ({
      name,
      overrideBlink: custom[name]?.overrideBlink ?? null
    }))
  }, null, 2))
  process.exit(0)
}

const vrm0 = gltf.extensions?.VRM
if (vrm0) {
  const groups = vrm0.blendShapeMaster?.blendShapeGroups ?? []
  console.log(JSON.stringify({
    spec: 'VRM 0.x',
    total: groups.length,
    expressions: groups.map((group) => ({
      name: group.name ?? null,
      presetName: group.presetName ?? null
    }))
  }, null, 2))
  process.exit(0)
}

throw new Error('No VRM extension found')
