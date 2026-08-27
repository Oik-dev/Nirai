import fs from 'node:fs'

const filePath = process.argv[2]
if (!filePath) throw new Error('VRM path required')
const buffer = fs.readFileSync(filePath)
if (buffer.toString('utf8', 0, 4) !== 'glTF') throw new Error('Not GLB')
const jsonLength = buffer.readUInt32LE(12)
const json = JSON.parse(buffer.toString('utf8', 20, 20 + jsonLength).replace(/\u0000+$/g, ''))
const materials = json.materials ?? []
const accessors = json.accessors ?? []
const meshes = json.meshes ?? []
const nodes = json.nodes ?? []

const rows = []
for (let ni = 0; ni < nodes.length; ni++) {
  const node = nodes[ni]
  if (node.mesh == null) continue
  const mesh = meshes[node.mesh]
  for (let pi = 0; pi < (mesh?.primitives?.length ?? 0); pi++) {
    const prim = mesh.primitives[pi]
    const pos = accessors[prim.attributes?.POSITION]
    const mat = materials[prim.material] ?? {}
    const min = pos?.min ?? null
    const max = pos?.max ?? null
    const size = min && max ? max.map((v, i) => max[i] - min[i]) : null
    rows.push({
      nodeIndex: ni,
      node: node.name ?? null,
      meshIndex: node.mesh,
      mesh: mesh?.name ?? null,
      primitive: pi,
      positionCount: pos?.count ?? null,
      min,
      max,
      size,
      materialIndex: prim.material ?? null,
      material: mat.name ?? null,
      alphaMode: mat.alphaMode ?? 'OPAQUE',
      alphaCutoff: mat.alphaCutoff ?? null,
      doubleSided: mat.doubleSided ?? false,
      baseColorFactor: mat.pbrMetallicRoughness?.baseColorFactor ?? null,
      baseColorTexture: mat.pbrMetallicRoughness?.baseColorTexture?.index ?? null,
      mtoon: mat.extensions?.VRMC_materials_mtoon ?? null
    })
  }
}
rows.sort((a,b) => {
  const area = (r) => r.size ? Math.max(r.size[0]*r.size[1], r.size[0]*r.size[2], r.size[1]*r.size[2]) : 0
  return area(b) - area(a)
})
console.log(JSON.stringify(rows.slice(0, 80), null, 2))
