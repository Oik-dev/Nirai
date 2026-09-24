import fs from 'node:fs/promises'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { VRMLoaderPlugin } from '@pixiv/three-vrm'
import { VRMAnimationLoaderPlugin, createVRMAnimationClip } from '@pixiv/three-vrm-animation'

if (typeof globalThis.self === 'undefined') globalThis.self = globalThis
if (typeof globalThis.createImageBitmap !== 'function') {
  globalThis.createImageBitmap = async () => ({ width: 1, height: 1, close() {} })
}

const avatarPath = path.resolve(process.argv[2])
const animationPath = path.resolve(process.argv[3])
if (!process.argv[2] || !process.argv[3]) {
  console.error('Usage: node scripts/diagnose-vrma-retarget.mjs <avatar.vrm> <animation.vrma>')
  process.exit(2)
}

const exactArrayBuffer = (buffer) => buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength)

async function parseVrm(filePath) {
  const bytes = await fs.readFile(filePath)
  const loader = new GLTFLoader()
  loader.register((parser) => new VRMLoaderPlugin(parser))
  const gltf = await loader.parseAsync(exactArrayBuffer(bytes), pathToFileURL(path.dirname(filePath) + path.sep).href)
  if (!gltf.userData.vrm) throw new Error('No VRM in avatar')
  return gltf.userData.vrm
}

async function parseVrma(filePath) {
  const bytes = await fs.readFile(filePath)
  const loader = new GLTFLoader()
  loader.register((parser) => new VRMAnimationLoaderPlugin(parser))
  const gltf = await loader.parseAsync(exactArrayBuffer(bytes), pathToFileURL(path.dirname(filePath) + path.sep).href)
  const animation = gltf.userData.vrmAnimations?.[0]
  if (!animation) throw new Error('No VRM animation')
  return animation
}

function q(node) {
  if (!node) return null
  return node.quaternion.toArray().map((v) => Number(v.toFixed(6)))
}
function p(node) {
  if (!node) return null
  return node.position.toArray().map((v) => Number(v.toFixed(6)))
}
function parentChain(node) {
  const names = []
  let current = node
  for (let i = 0; current && i < 12; i += 1) {
    names.push(current.name)
    current = current.parent
  }
  return names
}

const vrm = await parseVrm(avatarPath)
const animation = await parseVrma(animationPath)
const clip = createVRMAnimationClip(animation, vrm)
const mixer = new THREE.AnimationMixer(vrm.scene)
const action = mixer.clipAction(clip)

const boneNames = ['hips','spine','chest','upperChest','leftUpperLeg','leftLowerLeg','leftFoot','rightUpperLeg','rightLowerLeg','rightFoot','leftUpperArm','rightUpperArm']
const nodes = Object.fromEntries(boneNames.map((name) => [name, vrm.humanoid?.getNormalizedBoneNode(name) ?? null]))
const rawNodes = Object.fromEntries(boneNames.map((name) => [name, vrm.humanoid?.getRawBoneNode(name) ?? null]))

const before = Object.fromEntries(boneNames.map((name) => [name, { q: q(nodes[name]), p: p(nodes[name]) }]))
const rawBefore = Object.fromEntries(boneNames.map((name) => [name, { q: q(rawNodes[name]), p: p(rawNodes[name]) }]))
action.play()
mixer.update(0)
mixer.update(Math.min(0.5, Math.max(1 / 60, clip.duration * 0.125)))
vrm.update(1 / 60)
const after = Object.fromEntries(boneNames.map((name) => [name, { q: q(nodes[name]), p: p(nodes[name]) }]))
const rawAfter = Object.fromEntries(boneNames.map((name) => [name, { q: q(rawNodes[name]), p: p(rawNodes[name]) }]))

const result = {
  avatar: path.basename(avatarPath),
  animation: path.basename(animationPath),
  duration: clip.duration,
  trackCount: clip.tracks.length,
  tracks: clip.tracks.map((track) => track.name),
  rawBoneNames: Object.fromEntries(boneNames.map((name) => [name, rawNodes[name]?.name ?? null])),
  normalizedParentChains: Object.fromEntries(boneNames.map((name) => [name, parentChain(nodes[name])])),
  before,
  after,
  rawBefore,
  rawAfter,
}
console.log(JSON.stringify(result, null, 2))
