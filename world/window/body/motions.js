import * as THREE from 'three';
import { VRMAnimationLoaderPlugin, createVRMAnimationHumanoidTracks } from '@pixiv/three-vrm-animation';
import { parseGlb, selfContainedLoader } from './gltf.js';

// 覚えた動き（.vrma）を読む。
export async function parseMotion(bytes) {
  const loader = selfContainedLoader();
  loader.register(parser => new VRMAnimationLoaderPlugin(parser));
  const animation = (await parseGlb(loader, bytes)).userData.vrmAnimations?.[0];
  if (!animation) throw new Error('動きを読み込めませんでした。');
  return animation;
}

// 組み込みの身振りも覚えた動きも、ここで同じ AnimationClip になる（VRM 0.x の向きの直しもここ）。
// 使うのは骨の動きだけ。表情は本人が選び、目は視線が持つので、.vrma の表情と視線の動きは使わない。
export function motionClip(name, animation, vrm) {
  const { translation, rotation } = createVRMAnimationHumanoidTracks(animation, vrm.humanoid, vrm.meta?.metaVersion);
  return new THREE.AnimationClip(name, animation.duration, [...translation.values(), ...rotation.values()]);
}
