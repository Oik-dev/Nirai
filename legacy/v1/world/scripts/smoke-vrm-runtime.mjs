import fs from 'node:fs/promises'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm'
import {
  createVRMAnimationClip,
  VRMAnimationLoaderPlugin
} from '@pixiv/three-vrm-animation'

// GLTFLoader assumes browser globals even when parsing from an ArrayBuffer.
// Keep the smoke test headless by aliasing `self` and providing the minimal
// ImageBitmap-shaped object needed for texture construction. Texture pixels are
// validated visually in the real renderer; texture/material references are
// checked separately by inspect-vrm-meshes.mjs.
if (typeof globalThis.self === 'undefined') {
  globalThis.self = globalThis
}
if (typeof globalThis.createImageBitmap !== 'function') {
  globalThis.createImageBitmap = async () => ({
    width: 1,
    height: 1,
    close() {}
  })
}

const avatarArg = process.argv[2]
const animationArgs = process.argv.slice(3)
if (!avatarArg) {
  console.error('Usage: node scripts/smoke-vrm-runtime.mjs <avatar.vrm> [animation.vrma ...]')
  process.exit(2)
}

const avatarPath = path.resolve(avatarArg)
const animationPaths = animationArgs.map((value) => path.resolve(value))

function exactArrayBuffer(buffer) {
  return buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength)
}

async function parseVrm(filePath) {
  const bytes = await fs.readFile(filePath)
  const loader = new GLTFLoader()
  loader.register((parser) => new VRMLoaderPlugin(parser))
  const gltf = await loader.parseAsync(exactArrayBuffer(bytes), pathToFileURL(path.dirname(filePath) + path.sep).href)
  const vrm = gltf.userData.vrm
  if (!vrm) {
    VRMUtils.deepDispose(gltf.scene)
    throw new Error('Parsed GLB does not expose gltf.userData.vrm')
  }
  VRMUtils.rotateVRM0(vrm)
  VRMUtils.removeUnnecessaryVertices(vrm.scene)
  vrm.scene.traverse((object) => {
    object.frustumCulled = false
  })
  return vrm
}

async function parseVrma(filePath, vrm) {
  const bytes = await fs.readFile(filePath)
  const loader = new GLTFLoader()
  loader.register((parser) => new VRMAnimationLoaderPlugin(parser))
  const gltf = await loader.parseAsync(exactArrayBuffer(bytes), pathToFileURL(path.dirname(filePath) + path.sep).href)
  const animation = gltf.userData.vrmAnimations?.[0]
  if (!animation) {
    throw new Error('VRMA contains no VRM animation')
  }
  return createVRMAnimationClip(animation, vrm)
}

const REQUIRED_BONES = [
  'hips', 'spine', 'chest', 'upperChest', 'neck', 'head',
  'leftUpperLeg', 'leftLowerLeg', 'leftFoot',
  'rightUpperLeg', 'rightLowerLeg', 'rightFoot',
  'leftUpperArm', 'leftLowerArm', 'leftHand',
  'rightUpperArm', 'rightLowerArm', 'rightHand'
]
const REQUIRED_EXPRESSIONS = [
  'neutral', 'happy', 'angry', 'sad', 'relaxed', 'surprised',
  'aa', 'ih', 'ou', 'ee', 'oh', 'blink'
]

const vrm = await parseVrm(avatarPath)
const missingBones = REQUIRED_BONES.filter((name) => !vrm.humanoid?.getNormalizedBoneNode(name))
const expressionNames = vrm.expressionManager?.expressions.map((expression) => expression.expressionName) ?? []
const missingExpressions = REQUIRED_EXPRESSIONS.filter((name) => !expressionNames.includes(name))

const sceneBounds = new THREE.Box3().setFromObject(vrm.scene)
const sceneSize = sceneBounds.getSize(new THREE.Vector3())
const hips = vrm.humanoid?.getNormalizedBoneNode('hips')
const head = vrm.humanoid?.getNormalizedBoneNode('head')
const hipsPosition = hips?.getWorldPosition(new THREE.Vector3()) ?? null
const headPosition = head?.getWorldPosition(new THREE.Vector3()) ?? null

const springBoneManager = vrm.springBoneManager ?? null
const springJoints = springBoneManager ? Array.from(springBoneManager.joints) : []
let springFinite = true
let springError = null
try {
  springBoneManager?.setInitState()
  for (let frame = 0; frame < 120; frame += 1) {
    vrm.update(1 / 60)
  }
  springFinite = springJoints.every((joint) => {
    const q = joint.bone.quaternion
    return Number.isFinite(q.x) && Number.isFinite(q.y) && Number.isFinite(q.z) && Number.isFinite(q.w)
  })
} catch (error) {
  springFinite = false
  springError = error instanceof Error ? `${error.name}: ${error.message}` : String(error)
}

const animationResults = []
for (const animationPath of animationPaths) {
  const clip = await parseVrma(animationPath, vrm)
  const mixer = new THREE.AnimationMixer(vrm.scene)
  const action = mixer.clipAction(clip)
  action.play()
  mixer.update(0)
  mixer.update(1 / 60)
  vrm.update(1 / 60)
  animationResults.push({
    file: path.basename(animationPath),
    name: clip.name,
    duration: clip.duration,
    trackCount: clip.tracks.length,
    hasHipsPositionTrack: clip.tracks.some((track) => track.name.endsWith('NormalizedHips.position')),
    hasHumanoidRotationTrack: clip.tracks.some((track) => /Normalized.+\.quaternion$/.test(track.name))
  })
  action.stop()
  mixer.uncacheRoot(vrm.scene)
}

const result = {
  avatar: avatarPath,
  specVersion: vrm.meta?.metaVersion ?? null,
  missingBones,
  missingExpressions,
  availableExpressionCount: expressionNames.length,
  availableExpressions: expressionNames,
  sceneSize: sceneSize.toArray(),
  hipsPosition: hipsPosition?.toArray() ?? null,
  headPosition: headPosition?.toArray() ?? null,
  animations: animationResults,
  springBone: {
    present: springBoneManager !== null,
    jointCount: springJoints.length,
    colliderCount: springBoneManager?.colliders.length ?? 0,
    finiteAfter120Frames: springFinite,
    error: springError,
    sampleBones: springJoints.slice(0, 12).map((joint) => joint.bone.name),
    centers: [...new Set(springJoints.map((joint) => joint.center?.name ?? null))]
  }
}

console.log(JSON.stringify(result, null, 2))
VRMUtils.deepDispose(vrm.scene)

if (missingBones.length > 0 || missingExpressions.length > 0 || !springFinite) {
  process.exitCode = 1
}
