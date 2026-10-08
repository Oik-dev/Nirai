import * as THREE from 'three';
import { VRMAnimation } from '@pixiv/three-vrm-animation';
import { REST_POSE, modelRotation } from './pose.js';
export { GESTURE_NAMES } from './catalog.js';

// 組み込みの身振り。キーフレーム [秒, x, y, z]（角度の約束は pose.js）をなめらかにつないで、.vrma と同じ VRMAnimation にする。
// 腕の最初と最後は基準姿勢。書いていない骨は動かさない（基準姿勢と揺らぎのまま）。
const rest = bone => REST_POSE[bone];
export const GESTURES = {
  うなずく: { seconds: 1.1, bones: {
    neck: [[0, 0, 0, 0], [.28, 7, 0, 0], [.5, 1, 0, 0], [.72, 4, 0, 0], [1.1, 0, 0, 0]],
    head: [[0, 0, 0, 0], [.28, 11, 0, 0], [.5, 1, 0, 0], [.72, 6, 0, 0], [1.1, 0, 0, 0]],
  } },
  首を振る: { seconds: 1.5, bones: {
    neck: [[0, 0, 0, 0], [.22, 0, -8, 0], [.5, 0, 8, 0], [.8, 0, -6, 0], [1.1, 0, 3, 0], [1.5, 0, 0, 0]],
    head: [[0, 0, 0, 0], [.22, 0, -12, 0], [.5, 0, 12, 0], [.8, 0, -9, 0], [1.1, 0, 4, 0], [1.5, 0, 0, 0]],
  } },
  首をかしげる: { seconds: 2.2, bones: {
    neck: [[0, 0, 0, 0], [.45, 2, 0, 6], [1.6, 2, 0, 6], [2.2, 0, 0, 0]],
    head: [[0, 0, 0, 0], [.45, 3, 4, 12], [1.6, 3, 4, 12], [2.2, 0, 0, 0]],
  } },
  小さく手を振る: { seconds: 2.6, bones: {
    // 上腕のひねり（x）で前腕を左右に振る。内へ振りすぎると手が口元を覆うので、外寄りで往復する。
    rightUpperArm: [[0, ...rest('rightUpperArm')], [.5, -10, 29, 74], [.75, -2, 29, 74], [1, -18, 29, 74], [1.25, -2, 29, 74],
      [1.5, -18, 29, 74], [1.75, -4, 29, 74], [2, -10, 29, 74], [2.6, ...rest('rightUpperArm')]],
    rightLowerArm: [[0, ...rest('rightLowerArm')], [.5, 90, 120, 0], [2, 90, 120, 0], [2.6, ...rest('rightLowerArm')]],
  } },
  身体を傾ける: { seconds: 2.6, bones: {
    spine: [[0, 0, 0, 0], [.6, 0, 0, 5], [1.9, 0, 0, 5], [2.6, 0, 0, 0]],
    chest: [[0, 0, 0, 0], [.6, 0, 0, 5], [1.9, 0, 0, 5], [2.6, 0, 0, 0]],
    neck: [[0, 0, 0, 0], [.6, 0, 0, -3], [1.9, 0, 0, -3], [2.6, 0, 0, 0]],
    head: [[0, 0, 0, 0], [.6, 0, 0, -2], [1.9, 0, 0, -2], [2.6, 0, 0, 0]],
  } },
  おじぎ: { seconds: 2.4, bones: {
    spine: [[0, 0, 0, 0], [.6, 12, 0, 0], [1.3, 12, 0, 0], [2, 0, 0, 0], [2.4, 0, 0, 0]],
    chest: [[0, 0, 0, 0], [.6, 10, 0, 0], [1.3, 10, 0, 0], [2, 0, 0, 0], [2.4, 0, 0, 0]],
    neck: [[0, 0, 0, 0], [.6, 8, 0, 0], [1.3, 8, 0, 0], [2, 0, 0, 0], [2.4, 0, 0, 0]],
    head: [[0, 0, 0, 0], [.6, 6, 0, 0], [1.3, 6, 0, 0], [2, 0, 0, 0], [2.4, 0, 0, 0]],
  } },
};
const FRAMES_PER_SECOND = 30;

// キーフレームのあいだを、両端で止まるなめらかな曲線でつなぐ（キーフレームは動きの端なので、そこで一瞬止まるのが自然）。
export function angleAt(keys, t) {
  const next = keys.findIndex(key => key[0] >= t);
  if (next === -1) return keys.at(-1).slice(1);
  if (next === 0) return keys[0].slice(1);
  const [from, to] = [keys[next - 1], keys[next]];
  const x = (t - from[0]) / (to[0] - from[0]);
  const s = x * x * (3 - 2 * x);
  return [1, 2, 3].map(i => from[i] + (to[i] - from[i]) * s);
}

export function gestureAnimation(name) {
  const gesture = GESTURES[name];
  if (!gesture) return null;
  const animation = new VRMAnimation();
  animation.duration = gesture.seconds;
  const frames = Math.round(gesture.seconds * FRAMES_PER_SECOND);
  const rotation = new THREE.Quaternion();
  for (const [bone, keys] of Object.entries(gesture.bones)) {
    const times = new Float32Array(frames + 1);
    const values = new Float32Array((frames + 1) * 4);
    for (let frame = 0; frame <= frames; frame++) {
      times[frame] = frame / FRAMES_PER_SECOND;
      modelRotation(angleAt(keys, frame / FRAMES_PER_SECOND), '1', rotation).toArray(values, frame * 4);
    }
    animation.humanoidTracks.rotation.set(bone, new THREE.QuaternionKeyframeTrack(`${bone}.quaternion`, times, values));
  }
  return animation;
}
