import fs from 'node:fs'

const filePath = process.argv[2]
if (!filePath) throw new Error('VRM path required')
const buffer = fs.readFileSync(filePath)
if (buffer.toString('utf8', 0, 4) !== 'glTF') throw new Error('Not GLB')
const jsonLength = buffer.readUInt32LE(12)
const json = JSON.parse(buffer.toString('utf8', 20, 20 + jsonLength).replace(/\u0000+$/g, ''))
const nodes = json.nodes ?? []
const ext = json.extensions?.VRMC_springBone ?? null

if (!ext) {
  console.log(JSON.stringify({ present: false, springs: [], colliders: [], colliderGroups: [] }, null, 2))
  process.exit(0)
}

const springs = (ext.springs ?? []).map((spring, index) => ({
  index,
  name: spring.name ?? null,
  center: spring.center == null ? null : { index: spring.center, name: nodes[spring.center]?.name ?? null },
  colliderGroups: spring.colliderGroups ?? [],
  joints: (spring.joints ?? []).map((joint, jointIndex) => ({
    jointIndex,
    nodeIndex: joint.node,
    node: nodes[joint.node]?.name ?? null,
    hitRadius: joint.hitRadius ?? 0,
    stiffness: joint.stiffness ?? 1,
    gravityPower: joint.gravityPower ?? 0,
    gravityDir: joint.gravityDir ?? [0, -1, 0],
    dragForce: joint.dragForce ?? 0.5,
    role: jointIndex === (spring.joints?.length ?? 0) - 1 ? 'tail-marker' : 'simulated'
  }))
}))

const colliders = (ext.colliders ?? []).map((collider, index) => ({
  index,
  nodeIndex: collider.node,
  node: nodes[collider.node]?.name ?? null,
  shape: collider.shape ?? null
}))
const colliderGroups = (ext.colliderGroups ?? []).map((group, index) => ({
  index,
  name: group.name ?? null,
  colliders: group.colliders ?? []
}))

console.log(JSON.stringify({
  present: true,
  specVersion: ext.specVersion ?? null,
  springCount: springs.length,
  simulatedJointCount: springs.reduce((sum, spring) => sum + Math.max(0, spring.joints.length - 1), 0),
  colliderCount: colliders.length,
  colliderGroupCount: colliderGroups.length,
  springs,
  colliders,
  colliderGroups
}, null, 2))
