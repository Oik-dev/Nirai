import fs from 'node:fs'

const filePath = process.argv[2]
if (!filePath) throw new Error('VRM path required')
const buffer = fs.readFileSync(filePath)
if (buffer.toString('utf8', 0, 4) !== 'glTF') throw new Error('Not GLB')
const jsonLength = buffer.readUInt32LE(12)
const json = JSON.parse(buffer.toString('utf8', 20, 20 + jsonLength).replace(/\u0000+$/g, ''))
const materials = json.materials ?? []
const rows = materials.map((material) => ({
  name: material.name ?? null,
  alphaMode: material.alphaMode ?? 'OPAQUE',
  baseColorTexture: material.pbrMetallicRoughness?.baseColorTexture?.index ?? null,
  mtoon: material.extensions?.VRMC_materials_mtoon != null
}))
const missingMToon = rows.filter((row) => !row.mtoon).map((row) => row.name)
const expectedMaterialCount = Number(process.argv[3] ?? 17)
if (!Number.isInteger(expectedMaterialCount) || expectedMaterialCount < 1) {
  throw new Error('Expected material count must be a positive integer')
}
const ok = materials.length === expectedMaterialCount && missingMToon.length === 0
console.log(JSON.stringify({
  ok,
  materialCount: materials.length,
  expectedMaterialCount,
  mtoonCount: rows.filter((row) => row.mtoon).length,
  missingMToon,
  alphaBlendCount: rows.filter((row) => row.alphaMode === 'BLEND').length,
  texturedCount: rows.filter((row) => row.baseColorTexture != null).length,
  materials: rows
}, null, 2))
if (!ok) process.exitCode = 1
