import fs from 'node:fs'

const filePath = process.argv[2]
if (!filePath) throw new Error('VRM path required')
const buffer = fs.readFileSync(filePath)
if (buffer.toString('utf8', 0, 4) !== 'glTF') throw new Error('Not GLB')
const jsonLength = buffer.readUInt32LE(12)
const json = JSON.parse(buffer.toString('utf8', 20, 20 + jsonLength).replace(/\u0000+$/g, ''))
const rows = (json.materials ?? []).map((material) => ({
  name: material.name ?? null,
  normalTexture: material.normalTexture?.index ?? null,
  normalScale: material.normalTexture?.scale ?? null,
  baseColorTexture: material.pbrMetallicRoughness?.baseColorTexture?.index ?? null,
  shadeTexture: material.extensions?.VRMC_materials_mtoon?.shadeMultiplyTexture?.index ?? null,
  rimColor: material.extensions?.VRMC_materials_mtoon?.parametricRimColorFactor ?? null
}))
console.log(JSON.stringify({
  normalMapped: rows.filter((row) => row.normalTexture != null).map((row) => row.name),
  normalMappedCount: rows.filter((row) => row.normalTexture != null).length,
  rows
}, null, 2))
