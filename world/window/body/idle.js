import * as THREE from 'three';
import { smoothNoise } from './noise.js';
import { modelRotation } from './pose.js';

// 待機の揺らぎ：呼吸・漂い・体の小さな揺れ。型は「待機」と「話している最中」の2つで、話し始め・話し終わりに
// 約0.5秒かけて混ぜて移る（AIRI の MAGIC の型の分け方を借りた）。揺れは雑音から作るので、同じ型を繰り返さない。
// 角度は pose.js の約束（VRM 1.0 の向き、度）。[骨, 軸, 待機の振れ幅, 待機の速さ(Hz), 話す振れ幅, 話す速さ(Hz)]
export const SWAY = Object.freeze([
  ['hips', 'x', .8, .07, 1, .09], ['hips', 'z', .8, .05, 1, .07],
  ['spine', 'y', 1.5, .06, 2, .1], ['spine', 'z', .8, .08, 1, .1],
  ['chest', 'x', .8, .09, 1.2, .15],
  ['neck', 'x', 1.5, .1, 2.5, .25], ['neck', 'y', 2, .07, 3, .18], ['neck', 'z', 1.2, .08, 2, .15],
  ['head', 'x', 1.5, .12, 3.5, .35], ['head', 'y', 2.5, .09, 4, .22], ['head', 'z', 1.5, .1, 2.5, .2],
  ['leftUpperArm', 'x', 2, .08, 2, .1], ['leftUpperArm', 'y', 3, .07, 3, .09], ['leftUpperArm', 'z', 2.5, .11, 2.5, .13],
  ['rightUpperArm', 'x', 2, .08, 2, .1], ['rightUpperArm', 'y', 3, .07, 3, .09], ['rightUpperArm', 'z', 2.5, .11, 2.5, .13],
  ['leftLowerArm', 'y', 5, .1, 5, .12], ['rightLowerArm', 'y', 5, .1, 5, .12],
  ['leftHand', 'z', 5, .13, 5, .15], ['rightHand', 'z', 5, .13, 5, .15],
  ['leftUpperLeg', 'x', 2.5, .06, 2.5, .07], ['rightUpperLeg', 'x', 2.5, .06, 2.5, .07],
  ['leftLowerLeg', 'x', 4, .08, 4, .09], ['rightLowerLeg', 'x', 4, .08, 4, .09],
]);
// 呼吸：待機は1分に13回ほど、話す間は18回ほど。速さは雑音で±15%ゆらぐ。
export const BREATH = Object.freeze({ calmHz: .22, speakingHz: .3, chest: 1.2, spine: .5, shoulders: 1.2, rise: .0015 });
// 漂い：体全体（vrm.scene）が水の中でゆっくり上下・前後左右に動き、少し向きを変える。
export const DRIFT = Object.freeze({ x: .006, y: .009, z: .006, hz: .05, yaw: 1.5, roll: .8 });
const PROFILE_SECONDS = .25; // 混ざり具合が 1/e になるまで。0.5秒でほぼ移り終える。

const AXES = { x: 0, y: 1, z: 2 };

export class IdleMotion {
  constructor(vrm, seed = 1) {
    this.vrm = vrm;
    this.metaVersion = vrm.meta?.metaVersion;
    this.mix = 0;
    this.channels = SWAY.map(([bone, axis, calmAmp, calmHz, speakAmp, speakHz], index) => ({
      node: vrm.humanoid.getNormalizedBoneNode(bone), axis: AXES[axis], calmAmp, calmHz, speakAmp, speakHz,
      noise: smoothNoise(seed * 7919 + index * 104729), phase: index * 3.7,
    })).filter(channel => channel.node);
    this.breath = { phase: 0, noise: smoothNoise(seed * 7919 + 1) };
    this.driftNoise = ['x', 'y', 'z', 'yaw', 'roll'].map((_, index) => smoothNoise(seed * 7919 + 11 + index));
    this.driftPhase = 0;
    this.nodes = Object.fromEntries(['spine', 'chest', 'leftUpperArm', 'rightUpperArm']
      .map(name => [name, vrm.humanoid.getNormalizedBoneNode(name)]));
    this.restPosition = vrm.scene.position.clone();
    this.restQuaternion = vrm.scene.quaternion.clone();
    this.offsets = new Map();
    this.rotation = new THREE.Quaternion();
  }

  // 基準姿勢と身振りの上に、揺らぎを後ろから掛ける（骨それぞれの向きで小さく回す）。delta が 0 なら止まったまま。
  update(delta, speaking) {
    const toward = speaking ? 1 : 0;
    this.mix += (toward - this.mix) * (1 - Math.exp(-delta / PROFILE_SECONDS));
    const mix = this.mix;
    this.offsets.clear();
    for (const channel of this.channels) {
      channel.phase += delta * THREE.MathUtils.lerp(channel.calmHz, channel.speakHz, mix);
      const angle = channel.noise(channel.phase) * THREE.MathUtils.lerp(channel.calmAmp, channel.speakAmp, mix);
      this.offset(channel.node)[channel.axis] += angle;
    }
    const breathHz = THREE.MathUtils.lerp(BREATH.calmHz, BREATH.speakingHz, mix) * (1 + .15 * this.breath.noise(this.breath.phase * .3));
    this.breath.phase += delta * breathHz;
    const breath = Math.sin(this.breath.phase * Math.PI * 2); // 1 で吸いきり
    const { spine, chest, leftUpperArm, rightUpperArm } = this.nodes;
    if (chest) this.offset(chest)[0] -= breath * BREATH.chest;
    if (spine) this.offset(spine)[0] -= breath * BREATH.spine;
    if (leftUpperArm) this.offset(leftUpperArm)[2] += breath * BREATH.shoulders;
    if (rightUpperArm) this.offset(rightUpperArm)[2] -= breath * BREATH.shoulders;
    for (const [node, euler] of this.offsets) node.quaternion.multiply(modelRotation(euler, this.metaVersion, this.rotation));

    this.driftPhase += delta * DRIFT.hz;
    const [x, y, z, yaw, roll] = this.driftNoise.map((noise, index) => noise(this.driftPhase + index * 17.3));
    this.vrm.scene.position.copy(this.restPosition)
      .add(new THREE.Vector3(x * DRIFT.x, y * DRIFT.y + breath * BREATH.rise, z * DRIFT.z));
    this.vrm.scene.quaternion.copy(this.restQuaternion)
      .multiply(modelRotation([0, yaw * DRIFT.yaw, roll * DRIFT.roll], '1', this.rotation));
  }

  offset(node) {
    let euler = this.offsets.get(node);
    if (!euler) this.offsets.set(node, euler = [0, 0, 0]);
    return euler;
  }
}
