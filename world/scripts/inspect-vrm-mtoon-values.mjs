import fs from 'node:fs'

const filePath = process.argv[2]
if (!filePath) throw new Error('VRM path required')
const buffer = fs.readFileSync(filePath)
if (buffer.toString('utf8', 0, 4) !== 'glTF') throw new Error('Not GLB')
const jsonLength = buffer.readUInt32LE(12)
const json = JSON.parse(buffer.toString('utf8', 20, 20 + jsonLength).replace(/\u0000+$/g, ''))

const rows = (json.materials ?? []).map((material) => {
  const pbr = material.pbrMetallicRoughness ?? {}
  const mtoon = material.extensions?.VRMC_materials_mtoon ?? null
  return {
    name: material.name ?? null,
    baseColorFactor: pbr.baseColorFactor ?? [1, 1, 1, 1],
    baseColorTexture: pbr.baseColorTexture?.index ?? null,
    alphaMode: material.alphaMode ?? 'OPAQUE',
    doubleSided: material.doubleSided ?? false,
    mtoon: mtoon ? {
      shadeColorFactor: mtoon.shadeColorFactor ?? [1, 1, 1],
      shadeMultiplyTexture: mtoon.shadeMultiplyTexture?.index ?? null,
      shadingShiftFactor: mtoon.shadingShiftFactor ?? 0,
      shadingToonyFactor: mtoon.shadingToonyFactor ?? 0.9,
      giEqualizationFactor: mtoon.giEqualizationFactor ?? 0.9,
      parametricRimColorFactor: mtoon.parametricRimColorFactor ?? [0, 0, 0],
      parametricRimFresnelPowerFactor: mtoon.parametricRimFresnelPowerFactor ?? 5,
      parametricRimLiftFactor: mtoon.parametricRimLiftFactor ?? 0,
      rimLightingMixFactor: mtoon.rimLightingMixFactor ?? 1,
      outlineWidthMode: mtoon.outlineWidthMode ?? 'none',
      emissiveFactor: material.emissiveFactor ?? [0, 0, 0],
    } : null,
  }
})

console.log(JSON.stringify({ file: filePath, materialCount: rows.length, materials: rows }, null, 2))
